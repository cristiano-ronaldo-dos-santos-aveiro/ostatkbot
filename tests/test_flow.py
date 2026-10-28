"""Сквозной сценарий: менеджер настраивает, бариста работает смену, отчёт сходится."""
from sqlalchemy import func, select

from bot.db.models import CancelRequest, Movement, Product, Sale, Shift, ShiftCount, Status

M, B, STRANGER = 1, 2, 3


async def stock(h, name: str) -> int:
    async with h.sessionmaker() as s:
        return await s.scalar(select(Product.stock).where(Product.name == name))


async def add_product(h, name, unit_label, sellable, price, min_stock):
    await h.send(M, "⚙️ Товары")
    await h.click(M, "Добавить товар")
    await h.click(M, unit_label)
    await h.send(M, name)
    await h.click(M, "Да" if sellable else "Нет")
    if sellable:
        await h.send(M, price)
    await h.send(M, min_stock)
    assert "Товар добавлен" in h.flow_text(M), h.flow_text(M)


async def income(h, name, qty):
    await h.send(M, "➕ Приход")
    await h.click(M, name)
    await h.send(M, qty)
    await h.click(M, "Пропустить")
    assert "Приход записан" in h.flow_text(M), h.flow_text(M)


async def setup_shop(h):
    await h.send(M, "/start")
    assert "главное меню менеджера" in h.last_text(M)

    await add_product(h, "Стакан M", "Штучный", False, None, "10")
    await add_product(h, "Круассан", "Штучный", True, "15 000", "2")
    await add_product(h, "Кофе зерно", "Граммовый", False, None, "500г")
    await add_product(h, "Молоко", "Миллилитры", False, None, "1л")
    await add_product(h, "Сироп ваниль", "Миллилитры", False, None, "0")

    # ошибка ввода не ломает сценарий
    await h.send(M, "⚙️ Товары")
    await h.click(M, "Добавить товар")
    await h.click(M, "Штучный")
    await h.send(M, "Стакан M")
    assert "уже есть" in h.flow_text(M)
    await h.click(M, "Отмена")

    await income(h, "Стакан M", "100")
    await income(h, "Круассан", "10")
    await income(h, "Кофе зерно", "2кг")
    await income(h, "Молоко", "5л")
    await income(h, "Сироп ваниль", "1л")
    assert await stock(h, "Кофе зерно") == 2000
    assert await stock(h, "Молоко") == 5000

    # напиток
    await h.send(M, "☕ Меню")
    await h.click(M, "Напитки")
    await h.click(M, "Добавить напиток")
    await h.send(M, "Капучино")
    await h.click(M, "S")
    await h.click(M, "M")
    await h.click(M, "Готово")
    await h.send(M, "18000")
    await h.click(M, "Стакан M")
    await h.send(M, "20 000")
    await h.click(M, "Стакан M")
    assert "Напиток добавлен" in h.flow_text(M)

    # добавка со списанием сиропа
    await h.click(M, "Назад")  # к списку напитков
    await h.click(M, "Назад")  # корень меню
    await h.click(M, "Добавки")
    await h.click(M, "Добавить добавку")
    await h.send(M, "Ваниль")
    await h.send(M, "5000")
    await h.click(M, "Да")
    await h.click(M, "Сироп ваниль")
    await h.send(M, "20мл")
    assert "Добавка создана" in h.flow_text(M)

    # быстрая кнопка
    await h.click(M, "Назад")
    await h.click(M, "Назад")
    await h.click(M, "Быстрые кнопки")
    await h.click(M, "Добавить быструю")
    await h.click(M, "Напиток")
    await h.click(M, "Капучино")
    await h.click(M, "M —")
    await h.click(M, "Без добавок")
    await h.click(M, "×1")
    await h.click(M, "Наличные")
    await h.click(M, "✅ Капучино M")
    assert "Кнопка добавлена" in h.flow_text(M)

    # бариста
    await h.send(M, "👥 Бариста")
    await h.click(M, "Добавить по ID")
    await h.send(M, "abc")
    assert "число" in h.flow_text(M)
    await h.send(M, str(B))
    await h.send(M, "Аня")
    assert "Бариста добавлен" in h.flow_text(M)


async def test_stranger_has_no_access(h):
    await h.send(STRANGER, "/start")
    assert "Нет доступа" in h.last_text(STRANGER)
    await h.send(STRANGER, "🛒 Продажа")
    assert "Нет доступа" in h.last_text(STRANGER)


async def test_invite_link(h):
    await h.send(M, "/start")
    await h.send(M, "👥 Бариста")
    await h.click(M, "Ссылка-приглашение")
    await h.send(M, "Боб")
    text = h.flow_text(M)
    token = text.split("start=inv_")[1].split()[0]
    await h.send(STRANGER, f"/start inv_{token}")
    assert "Добро пожаловать" in h.last_text(STRANGER)
    # повторно ссылку не использовать
    await h.send(4, f"/start inv_{token}")
    assert "недействительна" in h.last_text(4)


async def test_full_shift(h):
    await setup_shop(h)

    # бариста без смены не продаёт
    await h.send(B, "/start")
    assert "смена не открыта" in h.last_text(B)
    await h.send(B, "🛒 Продажа")
    assert "Сначала откройте смену" in h.last_text(B)

    await h.send(B, "🟢 Открыть смену")
    await h.send(B, "100 000")
    assert any("Смена открыта" in t for t in h.texts(B))
    assert any("открыл(а) смену" in t for t in h.texts(M))
    await h.send(B, "🟢 Открыть смену")
    assert "уже открыта" in h.last_text(B)

    # продажа: Капучино M + Ваниль ×2, терминал
    await h.send(B, "🛒 Продажа")
    await h.click(B, "Напитки")
    await h.click(B, "Капучино")
    await h.click(B, "M —")
    await h.click(B, "Ваниль")
    await h.click(B, "Готово")
    await h.click(B, "×2")
    assert "50 000 сум" in h.flow_text(B)
    await h.click(B, "Терминал")
    assert "✅ Продано: Капучино M, + Ваниль ×2, терминал — 50 000 сум" in h.flow_text(B)
    assert await stock(h, "Стакан M") == 98
    assert await stock(h, "Сироп ваниль") == 960

    # продажа товара, ввод количества вручную, нал
    await h.click(B, "Новая продажа")
    await h.click(B, "Товары")
    await h.click(B, "Круассан")
    await h.click(B, "Ввести")
    await h.send(B, "0")
    assert "больше нуля" in h.flow_text(B)
    await h.send(B, "1")
    await h.click(B, "Наличные")
    assert "Круассан, нал — 15 000 сум" in h.flow_text(B)
    assert await stock(h, "Круассан") == 9

    # быстрая продажа
    await h.send(B, "⭐ Быстрые продажи")
    await h.click(B, "Капучино M")
    assert "Капучино M, без добавок, нал — 20 000 сум" in h.flow_text(B)
    assert await stock(h, "Стакан M") == 97

    # расход
    await h.send(B, "💸 Расход")
    await h.send(B, "10к")
    await h.send(B, "Молоко в магазине")
    await h.send(B, "без фото")
    assert "Нужно именно фото" in h.flow_text(B)
    await h.send(B, photo=True)
    assert "Расход записан: 10 000 сум" in h.flow_text(B)
    assert any("💸 Расход: Аня" in t for t in h.texts(M))

    # запрос на отмену круассана → менеджер одобряет
    await h.send(B, "⚠️ Запросить отмену")
    await h.click(B, "Круассан")
    await h.send(B, "Клиент передумал")
    assert "Запрос отправлен" in h.flow_text(B)
    assert any("просит отменить: Круассан" in t for t in h.texts(M))
    # повторный запрос на ту же продажу запрещён
    await h.send(B, "⚠️ Запросить отмену")
    answer = await h.click(B, "Круассан")
    assert "уже есть запрос" in answer
    await h.click(B, "Отмена")

    h.flow[M] = max(mid for mid, (t, _) in h.api.messages[M].items() if "просит отменить" in t)
    answer = await h.click(M, "✅ Отменить")
    assert answer == "Продажа отменена"
    assert await stock(h, "Круассан") == 10
    assert "Отмена одобрена" in h.last_text(B)
    assert await h.replay() == "Запрос уже обработан"

    # закрытие смены. Порядок: штучные (Круассан, Стакан M), граммовые, мл
    await h.send(B, "🔚 Закрыть смену")
    assert "Круассан" in h.flow_text(B)
    await h.send(B, "10")
    assert "Стакан M" in h.flow_text(B)
    await h.send(B, "95")  # ошиблись
    await h.click(B, "Назад")
    assert "введено ранее: 95 шт" in h.flow_text(B)
    await h.send(B, "96")  # ожидалось 97 → −1
    await h.send(B, "1.8кг")
    await h.send(B, "4.5л")
    await h.send(B, "960мл")
    assert "наличные" in h.flow_text(B)
    await h.send(B, "110 000")  # 100 000 + 20 000 − 10 000
    await h.send(B, "50000")
    await h.send(B, "0")
    confirm = h.flow_text(B)
    assert "Стакан M: 96 шт" in confirm and "110 000 сум" in confirm
    assert "ожид" not in confirm.lower()
    await h.click(B, "Подтвердить")
    assert "Смена закрыта ✅" in h.flow_text(B)

    report = next(t for t in h.texts(M) if t.startswith("📋 Смена: Аня"))
    assert "💰 Выручка: 70 000 сум" in report
    assert "💵 Нал: ожид. 110 000 сум / факт 110 000 сум / разница 0 сум" in report
    assert "💳 Терминал: ожид. 50 000 сум / факт 50 000 сум / разница 0 сум" in report
    assert "— 10 000 сум — Молоко в магазине" in report
    assert "Капучино M: 3 шт, 70 000 сум" in report
    assert "Стакан M: 97 / 96 / −1 шт" in report
    assert "Кофе зерно: 200 г (начало 2 кг, приход 0 г, списано 0 г, факт 1.8 кг)" in report
    assert "↩️ Отмены: 1, запросов: 1" in report
    assert "Итог: ⚠️ Расхождения: Стакан M −1 шт" in report

    # бариста не видит ожидаемых значений нигде
    for text in h.texts(B):
        assert "ожид" not in text.lower(), text
        assert "расхожд" not in text.lower(), text

    async with h.sessionmaker() as s:
        shift = await s.scalar(select(Shift))
        assert shift.status == Status.CLOSED and shift.fact_cash == 110000
        assert await s.scalar(select(func.count(ShiftCount.id))) == 5
        # инвентаризация: стакан −1, кофе −200, молоко −500
        inv = (await s.execute(select(Movement.qty).where(Movement.type == "inventory"))).scalars().all()
        assert sorted(inv) == [-500, -200, -1]
        # остаток всегда равен сумме движений
        for p in (await s.scalars(select(Product))).all():
            total = await s.scalar(select(func.coalesce(func.sum(Movement.qty), 0)).where(Movement.product_id == p.id))
            assert p.stock == total, p.name
        assert await s.scalar(select(func.count(Sale.id)).where(Sale.status == Status.CANCELLED)) == 1
        assert (await s.scalar(select(CancelRequest.status))) == Status.APPROVED

    # меню бариста снова без продаж
    await h.send(B, "🛒 Продажа")
    assert "Сначала откройте смену" in h.last_text(B)

    # отчёты менеджера
    await h.send(M, "📊 Отчёты")
    await h.click(M, "Сегодня")
    summary = h.last_text(M)
    assert "💰 Выручка: 70 000 сум" in summary
    assert "Аня: выручка 70 000 сум, продаж 2, отмен 1, запросов на отмену 1" in summary
    assert "Стакан M −1 шт" in summary
    await h.send(M, "📊 Отчёты")
    await h.click(M, "Отчёт по смене")
    await h.click(M, "Аня")
    assert h.last_text(M).startswith("📋 Смена: Аня")

    # склад
    await h.send(M, "📦 Склад")
    assert "Стакан M: 96 шт" in h.last_text(M)


async def test_manager_cancel_and_writeoff(h):
    await setup_shop(h)
    await h.send(B, "/start")
    await h.send(B, "🟢 Открыть смену")
    await h.send(B, "0")
    await h.send(B, "⭐ Быстрые продажи")
    await h.click(B, "Капучино M")
    assert await stock(h, "Стакан M") == 99

    # менеджер отменяет продажу сам
    await h.send(M, "📜 Продажи")
    await h.click(M, "Аня")
    await h.click(M, "Капучино M")
    await h.click(M, "Отменить продажу")
    await h.send(M, "Тестовая продажа")
    assert "Продажа отменена" in h.flow_text(M)
    assert await stock(h, "Стакан M") == 100
    assert "Менеджер отменил продажу" in h.last_text(B)

    # списание ниже минимума → уведомление
    await h.send(M, "➖ Списание")
    await h.click(M, "Стакан M")
    await h.send(M, "95")
    await h.send(M, "Помялись")
    assert "Списано" in h.flow_text(M)
    assert any("ниже минимального остатка" in t for t in h.texts(M))

    # кнопка меню прерывает сценарий
    await h.send(M, "➕ Приход")
    await h.send(M, "📦 Склад")
    assert "Остатки на складе" in h.last_text(M)
