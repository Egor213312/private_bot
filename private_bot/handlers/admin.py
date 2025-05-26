from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from models import User, InviteLink, Subscription, Invite
from db import get_user_by_id
from utils.subscription_manager import create_subscription, check_subscription_status
from config import ADMIN_IDS
import os
import logging
from datetime import datetime, timedelta
import asyncio

logger = logging.getLogger(__name__)
router = Router()

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    logger.info(f"Checking admin rights for user_id: {user_id}")
    logger.info(f"ADMIN_IDS: {ADMIN_IDS}")
    is_admin = user_id in ADMIN_IDS
    logger.info(f"Is admin: {is_admin}")
    return is_admin

def get_user_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с действиями для пользователя"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="❌ Удалить пользователя",
                callback_data=f"delete_user_{user_id}"
            ),
            InlineKeyboardButton(
                text="🎁 Выдать подписку",
                callback_data=f"give_sub_{user_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession):
    """Обработчик команды /admin"""
    try:
        if not is_admin(message.from_user.id):
            await message.answer("У вас нет прав администратора")
            return

        help_text = (
            "👨‍💼 <b>Панель администратора</b>\n\n"
            "Доступные команды:\n"
            "📊 /admin_stats - Статистика бота\n"
            "📢 /admin_broadcast - Отправить сообщение всем пользователям\n"
            "👥 /admin_users - Список пользователей\n"
            "🎫 /activate_sub user_id months - Активировать подписку пользователю\n\n"
            "Пример: /activate_sub 123456789 3"
        )

        await message.answer(help_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in cmd_admin: {e}")
        await message.answer("Произошла ошибка при обработке команды")

@router.message(Command("admin_broadcast"))
async def cmd_admin_broadcast(message: Message, session: AsyncSession) -> None:
    """Обработчик команды /admin_broadcast"""
    try:
        # Проверяем права администратора
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ У вас нет прав для выполнения этой команды.")
            return

        # Получаем текст сообщения
        broadcast_text = message.text.replace("/admin_broadcast", "").strip()
        if not broadcast_text:
            await message.answer("❌ Укажите текст сообщения для рассылки.")
            return

        # Получаем всех пользователей
        users_query = select(User)
        result = await session.execute(users_query)
        users = result.scalars().all()

        if not users:
            await message.answer("❌ В базе данных нет пользователей для рассылки.")
            return

        # Отправляем сообщение каждому пользователю
        success_count = 0
        error_count = 0
        total_users = len(users)

        # Отправляем начальное сообщение о начале рассылки
        status_message = await message.answer(
            f"📢 Начало рассылки...\n"
            f"Всего пользователей: {total_users}\n"
            f"Отправлено: 0/{total_users}\n"
            f"Ошибок: 0"
        )

        for i, user in enumerate(users, 1):
            try:
                await message.bot.send_message(
                    chat_id=user.telegram_id,
                    text=broadcast_text,
                    parse_mode="HTML"
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения пользователю {user.telegram_id}: {e}")
                error_count += 1

            # Обновляем статус каждые 10 сообщений или в конце
            if i % 10 == 0 or i == total_users:
                await status_message.edit_text(
                    f"📢 Рассылка в процессе...\n"
                    f"Всего пользователей: {total_users}\n"
                    f"Отправлено: {i}/{total_users}\n"
                    f"Успешно: {success_count}\n"
                    f"Ошибок: {error_count}"
                )

            # Небольшая задержка между сообщениями
            await asyncio.sleep(0.1)

        # Отправляем финальное сообщение о результатах
        await message.answer(
            f"✅ Рассылка завершена!\n\n"
            f"📊 Статистика:\n"
            f"Всего пользователей: {total_users}\n"
            f"Успешно отправлено: {success_count}\n"
            f"Ошибок: {error_count}"
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке команды admin_broadcast: {e}")
        await message.answer("❌ Произошла ошибка при выполнении рассылки.")

@router.message(Command("admin_users"))
async def cmd_admin_users(message: Message, session: AsyncSession):
    """Обработчик команды /admin_users - управление пользователями"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет доступа к админ-панели.")
        return

    try:
        # Получаем всех пользователей с их подписками
        query = select(User).options(joinedload(User.subscriptions))
        result = await session.execute(query)
        users = result.unique().scalars().all()

        if not users:
            await message.answer("📊 <b>Статистика:</b>\n\n❌ Пользователей не найдено", parse_mode="HTML")
            return

        # Формируем сообщение со статистикой
        stats_message = (
            "📊 <b>Статистика бота:</b>\n\n"
            f"👥 Всего пользователей: {len(users)}\n"
            f"✨ Активных подписок: {sum(1 for user in users if user.is_subscribed)}\n\n"
            "👤 <b>Список пользователей:</b>\n"
            "➖➖➖➖➖➖➖➖➖➖\n\n"
        )

        # Добавляем информацию о каждом пользователе
        for user in users:
            # Получаем активную подписку пользователя
            active_subscription = next((sub for sub in user.subscriptions if sub.is_active), None)

            user_info = (
                f"🆔 <code>{user.telegram_id}</code>\n"
                f"👤 {user.full_name}\n"
                f"📧 {user.email}\n"
                f"☎️ {user.phone}\n"
                f"💫 Подписка: {'✅ Активна' if active_subscription else '❌ Неактивна'}\n"
                "➖➖➖➖➖➖➖➖➖➖\n"
            )
            
            # Проверяем, не превысит ли сообщение лимит
            if len(stats_message + user_info) > 4096:
                await message.answer(stats_message, parse_mode="HTML")
                stats_message = user_info
            else:
                stats_message += user_info

            # Отправляем кнопки действий для каждого пользователя
            await message.answer(
                f"Действия для пользователя {user.full_name}:",
                reply_markup=get_user_actions_keyboard(user.id)
            )

        # Отправляем оставшуюся информацию
        if stats_message:
            await message.answer(stats_message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка при обработке команды admin_users: {e}")
        await message.answer("❌ Произошла ошибка при получении списка пользователей.")

@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message, session: AsyncSession):
    """Обработчик команды /admin_stats - показывает статистику бота"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет доступа к админ-панели.")
        return

    try:
        # Получаем всех пользователей с их подписками
        query = select(User).options(joinedload(User.subscriptions))
        result = await session.execute(query)
        users = result.unique().scalars().all()

        # Получаем количество пользователей за последние 24 часа
        day_ago = datetime.now() - timedelta(days=1)
        new_users = sum(1 for user in users if user.created_at >= day_ago)

        # Получаем количество активных подписок
        active_subs = sum(1 for user in users if user.is_subscribed)

        # Получаем количество выданных инвайт-ссылок
        invites_query = select(InviteLink)
        invites_result = await session.execute(invites_query)
        invites = invites_result.scalars().all()
        total_invites = len(invites)
        used_invites = sum(1 for invite in invites if invite.is_used)

        # Вычисляем конверсию
        total_users = len(users)
        sub_conversion = (active_subs / total_users * 100) if total_users > 0 else 0
        invite_conversion = (used_invites / total_invites * 100) if total_invites > 0 else 0

        stats_message = (
            "📊 <b>Статистика бота:</b>\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"📈 Новых за 24 часа: {new_users}\n"
            f"✨ Активных подписок: {active_subs}\n"
            f"🔗 Выдано инвайт-ссылок: {total_invites}\n"
            f"✅ Использовано ссылок: {used_invites}\n\n"
            "💫 <b>Конверсия:</b>\n"
            f"• Подписок: {sub_conversion:.1f}%\n"
            f"• Использования ссылок: {invite_conversion:.1f}%"
        )

        await message.answer(stats_message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await message.answer("❌ Произошла ошибка при получении статистики")

@router.callback_query(lambda c: c.data.startswith('delete_user_'))
async def process_delete_user(callback: CallbackQuery, bot: Bot, session: AsyncSession):
    """Обработчик удаления пользователя"""
    try:
        if not is_admin(callback.from_user.id):
            await callback.answer("У вас нет прав администратора")
            return

        user_id = int(callback.data.split('_')[2])
        
        async with session.begin():
            user = await session.get(User, user_id)
            if not user:
                await callback.answer("Пользователь не найден")
                return

            # Удаляем все инвайт-ссылки, созданные пользователем
            invite_links = await session.execute(
                select(InviteLink).where(InviteLink.created_by_id == user_id)
            )
            for invite_link in invite_links.scalars():
                await session.delete(invite_link)

            # Удаляем все инвайт-ссылки, использованные пользователем
            used_invites = await session.execute(
                select(InviteLink).where(InviteLink.used_by_id == user_id)
            )
            for invite in used_invites.scalars():
                invite.used_by_id = None
                invite.is_used = False
                invite.used_at = None

            # Попытка удалить пользователя из закрытого канала
            try:
                await bot.ban_chat_member(
                    chat_id=config.CLOSED_CHANNEL_ID,
                    user_id=user_id
                )
                await bot.unban_chat_member(
                    chat_id=config.CLOSED_CHANNEL_ID,
                    user_id=user_id
                )
                logger.info(f"User {user_id} removed from closed channel")
            except Exception as e:
                logger.error(f"Error removing user {user_id} from closed channel: {e}")

            # Удаляем пользователя из базы данных
            await session.delete(user)
            await session.commit()
            
            await callback.answer("Пользователь успешно удален")
            await callback.message.edit_text(
                text=f"Пользователь {user_id} был удален",
                reply_markup=None
            )
            logger.info(f"User {user_id} deleted from database")
            
    except Exception as e:
        logger.error(f"Error in process_delete_user: {e}")
        await callback.answer("Произошла ошибка при удалении пользователя")
        return

@router.callback_query(lambda c: c.data.startswith('give_sub_'))
async def process_give_subscription(callback: CallbackQuery, session: AsyncSession):
    """Обработка выдачи подписки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ У вас нет доступа к этой функции")
        return

    try:
        user_id = int(callback.data.split('_')[2])
        user = await get_user_by_id(session, user_id)
        
        if not user:
            await callback.answer("❌ Пользователь не найден")
            return

        # Проверяем, есть ли активная подписка
        is_active, _ = await check_subscription_status(session, user.id)
        if is_active:
            await callback.answer("⚠️ У пользователя уже есть активная подписка")
            return

        # Создаем новую подписку на 30 дней
        subscription = await create_subscription(
            session=session,
            user_id=user.id,
            duration_days=30,
            auto_renewal=False
        )
        
        await callback.answer("✅ Подписка успешно выдана")
        await callback.message.edit_text(
            f"🎁 Пользователю {user.full_name} выдана подписка на 30 дней\n"
            f"📅 Действует до: {subscription.end_date.strftime('%d.%m.%Y')}",
            reply_markup=None
        )
    
    except Exception as e:
        logger.error(f"Ошибка при выдаче подписки: {e}")
        await callback.answer("❌ Произошла ошибка при выдаче подписки")

@router.callback_query(lambda c: c.data.startswith('generate_invite_'))
async def process_generate_invite(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    """Обработка генерации инвайт-ссылки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ У вас нет доступа к этой функции")
        return

    try:
        user_id = int(callback.data.split('_')[2])
        user = await get_user_by_id(session, user_id)
        
        if not user:
            await callback.answer("❌ Пользователь не найден")
            return

        # Генерируем инвайт-ссылку
        invite_link = await generate_invite_link(session, user.id, bot)
        
        if not invite_link:
            await callback.answer("❌ Не удалось создать инвайт-ссылку")
            return

        await callback.answer("✅ Инвайт-ссылка создана")
        await callback.message.edit_text(
            f"🔗 Инвайт-ссылка для пользователя {user.full_name}:\n\n"
            f"{invite_link}",
            reply_markup=None
        )
    
    except Exception as e:
        logger.error(f"Ошибка при генерации инвайт-ссылки: {e}")
        await callback.answer("❌ Произошла ошибка при создании инвайт-ссылки") 