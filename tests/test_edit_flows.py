"""Редактирование и второстепенные разделы менеджера."""
from sqlalchemy import select
from test_flow import B, M, setup_shop, stock

from bot.db.models import Addon, DrinkSize, Expense, Product, QuickButton, Status, User


async def test_product_edits(h):
    await setup_shop(h)
    await h.send(M, "⚙️ Товары")
    await h.click(M, "Стакан M")
    await h.click(M, "Название")
    await h.send(M, "")  # пустое игнорируется фильтром текста? отправим пробел
    await h.send(M, "Стакан 350")
    assert "Название изменено" in h.flow_text(M)
    await h.click(M, "Мин. остаток")
    await h.send(M, "abc")
    assert "числом" in h.flow_text(M)
    await h.send(M, "20")
    assert "Мин. остаток: 20 шт" in h.flow_text(M)
    # включить продажу без цены → сначала спросит цену
    await h.click(M, "Продажа: выкл")
    await h.send(M, "3000")
    assert "Продаётся отдельно: да" in h.flow_text(M)
    await h.click(M, "Скрыть")
    assert "скрыт" in h.flow_text(M)
    await h.click(M, "Вернуть")
    assert "снова активен" in h.flow_text(M)
    await h.click(M, "Цена")
    await h.send(M, "3500")
    assert "Цена: 3 500 сум" in h.flow_text(M)


async def test_drink_and_addon_edits(h):
    await setup_shop(h)
    await h.send(M, "☕ Меню")
    await h.click(M, "Напитки")
    await h.click(M, "Капучино")
    await h.click(M, "Название")
    await h.send(M, "Капучино классик")
    assert "Капучино классик" in h.flow_text(M)
    await h.click(M, "⚙️ M")
    await h.click(M, "Цена")
    await h.send(M, "22000")
    assert "Цена: 22 000 сум" in h.flow_text(M)
    await h.click(M, "Стакан")
    await h.click(M, "Без стакана")
    assert "Стакан: без стакана" in h.flow_text(M)
    await h.click(M, "Скрыть размер")
    await h.click(M, "К напитку")
    assert "M — 22 000 сум · стакан: без стакана (🙈 скрыт)" in h.flow_text(M)
    await h.click(M, "Добавить размер")
    await h.click(M, "L")
    await h.send(M, "25000")
    await h.click(M, "Стакан M")
    assert "Размер L добавлен" in h.flow_text(M)
    await h.click(M, "Скрыть напиток")
    assert "(🙈 скрыт)" in h.flow_text(M)

    await h.click(M, "Назад")
    await h.click(M, "Назад")
    await h.click(M, "Добавки")
    await h.click(M, "Ваниль")
    await h.click(M, "Цена")
    await h.send(M, "0")
    assert "Цена: 0 сум" in h.flow_text(M)
    await h.click(M, "Списание товара")
    await h.click(M, "Не списывать")
    assert "не списывается" in h.flow_text(M)
    await h.click(M, "Списание товара")
    await h.click(M, "Молоко")
    await h.send(M, "50")
    assert "Молоко × 50 мл" in h.flow_text(M)
    await h.click(M, "Название")
    await h.send(M, "Ваниль сироп")
    await h.click(M, "Скрыть")

    async with h.sessionmaker() as s:
        addon = await s.scalar(select(Addon))
        assert addon.name == "Ваниль сироп" and not addon.is_active and addon.product_qty == 50
        sizes = (await s.scalars(select(DrinkSize))).all()
        assert {x.size for x in sizes} == {"S", "M", "L"}

    # быстрая кнопка на скрытый напиток не продаётся, а не падает
    await h.send(B, "/start")
    await h.send(B, "🟢 Открыть смену")
    await h.send(B, "0")
    await h.send(B, "⭐ Быстрые продажи")
    answer = await h.click(B, "Капучино M")
    assert "недоступен" in answer
    await h.send(B, "🛒 Продажа")
    answer = await h.click(B, "Напитки")
    assert "нет напитков" in answer.lower()

    # убрать быструю кнопку
    await h.send(M, "☕ Меню")
    await h.click(M, "Быстрые кнопки")
    await h.click(M, "Капучино M")
    assert "Сейчас не работает" in h.flow_text(M)
    await h.click(M, "Убрать кнопку")
    async with h.sessionmaker() as s:
        assert not (await s.scalar(select(QuickButton))).is_active


async def test_staff_expense_cancel_and_reports(h):
    await setup_shop(h)
    await h.send(M, "👥 Бариста")
    await h.click(M, "Аня")
    await h.click(M, "Переименовать")
    await h.send(M, "Анна")
    assert "Имя изменено" in h.flow_text(M)

    await h.send(B, "/start")
    await h.send(B, "🟢 Открыть смену")
    await h.send(B, "50000")
    await h.send(B, "💸 Расход")
    await h.send(B, "5000")
    await h.send(B, "Сахар")
    await h.send(B, photo=True)

    await h.send(M, "💸 Расходы")
    await h.click(M, "Анна")
    await h.click(M, "Сахар")
    await h.click(M, "Отменить расход")
    await h.send(M, "Ошибка")
    assert "Расход отменён" in h.flow_text(M)
    assert "Менеджер отменил расход" in h.last_text(B)
    async with h.sessionmaker() as s:
        assert (await s.scalar(select(Expense))).status == Status.CANCELLED

    for period in ("Вчера", "7 дней", "30 дней"):
        await h.send(M, "📊 Отчёты")
        await h.click(M, period)
        assert h.last_text(M).startswith("📊 Сводка")

    # деактивация: доступ закрыт
    await h.send(M, "👥 Бариста")
    await h.click(M, "Анна")
    await h.click(M, "Деактивировать")
    assert "открытая смена" in h.flow_text(M)
    await h.send(B, "🛒 Продажа")
    assert "Нет доступа" in h.last_text(B)
    await h.click(M, "Активировать")
    async with h.sessionmaker() as s:
        assert (await s.scalar(select(User).where(User.telegram_id == B))).is_active

    # менеджер не может добавить себя бариста
    await h.send(M, "👥 Бариста")
    await h.click(M, "Добавить по ID")
    await h.send(M, str(M))
    assert "менеджер" in h.flow_text(M)


async def test_close_shift_without_sales_and_writeoff_reason(h):
    await setup_shop(h)
    await h.send(M, "➖ Списание")
    await h.click(M, "Молоко")
    await h.send(M, "1л")
    await h.send(M, " ")
    assert await stock(h, "Молоко") == 5000
    await h.send(M, "Скисло")
    assert await stock(h, "Молоко") == 4000

    await h.send(B, "/start")
    await h.send(B, "🟢 Открыть смену")
    await h.send(B, "0")
    await h.send(B, "⚠️ Запросить отмену")
    assert "нет продаж" in h.last_text(B)
    await h.send(B, "🔚 Закрыть смену")
    # кнопка меню посреди закрытия прерывает сценарий
    await h.send(B, "🛒 Продажа")
    assert "Что продаём" in h.flow_text(B)
    await h.send(B, "🔚 Закрыть смену")
    for value in ["10", "100", "2кг", "4л", "1л", "0", "0", "0"]:
        await h.send(B, value)
    await h.click(B, "Подтвердить")
    assert "Смена закрыта" in h.flow_text(B)
    report = next(t for t in h.texts(M) if t.startswith("📋 Смена"))
    assert "Итог: ✅ Всё сходится" in report
    async with h.sessionmaker() as s:
        assert (await s.scalar(select(Product.stock).where(Product.name == "Молоко"))) == 4000
