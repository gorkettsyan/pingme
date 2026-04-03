from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    remind_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency: Mapped[str] = mapped_column(String(20), default="daily")  # daily or weekly
    reminder_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reminder_days: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "mon,wed,fri"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    shame_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    completions: Mapped[list["HabitCompletion"]] = relationship(back_populates="habit", cascade="all, delete-orphan")


class HabitCompletion(Base):
    __tablename__ = "habit_completions"
    __table_args__ = (UniqueConstraint("habit_id", "date", name="uq_habit_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    habit_id: Mapped[int] = mapped_column(Integer, ForeignKey("habits.id"))
    completed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    date: Mapped[date] = mapped_column(Date)

    habit: Mapped["Habit"] = relationship(back_populates="completions")


class CustomShameMessage(Base):
    __tablename__ = "custom_shame_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    level: Mapped[str] = mapped_column(String(20))  # gentle, sarcasm, dramatic, nuclear
    message: Mapped[str] = mapped_column(Text)  # Use {name} and {days} as placeholders
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # e.g. 150 problems; None for streak-based goals
    daily_quota: Mapped[int] = mapped_column(Integer, default=1)  # e.g. 3 per day
    unit: Mapped[str] = mapped_column(String(50), default="times")  # e.g. problems, hours, sessions
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminder_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    apscheduler_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    progress: Mapped[list["GoalProgress"]] = relationship(back_populates="goal", cascade="all, delete-orphan")


class GoalProgress(Base):
    __tablename__ = "goal_progress"
    __table_args__ = (UniqueConstraint("goal_id", "date", name="uq_goal_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal_id: Mapped[int] = mapped_column(Integer, ForeignKey("goals.id"))
    count: Mapped[int] = mapped_column(Integer, default=0)  # how many done that day
    date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    goal: Mapped["Goal"] = relationship(back_populates="progress")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    channel_type: Mapped[str] = mapped_column(String(50))  # telegram, whatsapp
    apprise_url: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
