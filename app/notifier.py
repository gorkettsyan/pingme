import apprise
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationChannel


async def get_user_channels(session: AsyncSession, user_id: str) -> list[NotificationChannel]:
    result = await session.execute(
        select(NotificationChannel).where(
            NotificationChannel.user_id == user_id,
            NotificationChannel.is_active == True,  # noqa: E712
        )
    )
    return list(result.scalars().all())


async def send_notification(session: AsyncSession, user_id: str, title: str, body: str) -> bool:
    channels = await get_user_channels(session, user_id)
    if not channels:
        return False

    ap = apprise.Apprise()
    for channel in channels:
        ap.add(channel.apprise_url)

    result = await ap.async_notify(title=title, body=body)
    return bool(result)


async def add_channel(
    session: AsyncSession,
    user_id: str,
    channel_type: str,
    apprise_url: str,
) -> NotificationChannel:
    channel = NotificationChannel(
        user_id=user_id,
        channel_type=channel_type,
        apprise_url=apprise_url,
        is_active=True,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return channel


async def remove_channel(session: AsyncSession, channel_id: int) -> bool:
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        return False
    await session.delete(channel)
    await session.commit()
    return True
