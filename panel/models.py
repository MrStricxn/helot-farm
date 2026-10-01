# panel/models.py
"""Скелет данных фермы: Account / Device / Proxy / Task (SQLAlchemy 2.x)."""
import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SqlaEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- Enums ---


class AccountStatus(str, enum.Enum):
    NEW = "NEW"
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    BANNED = "BANNED"


class DeviceStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY = "BUSY"


class TaskType(str, enum.Enum):
    SEND_MESSAGE = "SEND_MESSAGE"
    JOIN_GROUP = "JOIN_GROUP"
    WARMUP = "WARMUP"
    REGISTER_ACCOUNT = "REGISTER_ACCOUNT"


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# --- Entities ---


class Account(Base):
    """WhatsApp-аккаунт."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        SqlaEnum(AccountStatus), nullable=False, default=AccountStatus.NEW
    )
    registration_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # один аккаунт — одно устройство (обратная сторона one-to-one)
    device: Mapped["Device | None"] = relationship(
        "Device", back_populates="account", uselist=False
    )
    proxies: Mapped[list["Proxy"]] = relationship("Proxy", back_populates="account")
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="account", cascade="all, delete-orphan"
    )


class Device(Base):
    """Эмулятор/устройство. serial — идентификатор для helot."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[DeviceStatus] = mapped_column(
        SqlaEnum(DeviceStatus), nullable=False, default=DeviceStatus.OFFLINE
    )
    # one-to-one: одно устройство — один аккаунт (nullable = устройство свободно)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id"), unique=True, nullable=True
    )

    account: Mapped["Account | None"] = relationship("Account", back_populates="device")


class Proxy(Base):
    """Прокси-сервер."""

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assigned_to_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )

    account: Mapped["Account | None"] = relationship("Account", back_populates="proxies")


class Task(Base):
    """Задача для аккаунта."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    task_type: Mapped[TaskType] = mapped_column(SqlaEnum(TaskType), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[TaskStatus] = mapped_column(
        SqlaEnum(TaskStatus), nullable=False, default=TaskStatus.PENDING
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    account: Mapped["Account"] = relationship("Account", back_populates="tasks")
