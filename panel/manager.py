# panel/manager.py
"""Сердце панели: бизнес-логика фермы поверх SQLAlchemy-сессии."""
from sqlalchemy import select

try:
    from panel.models import (
        Account,
        AccountStatus,
        Device,
        DeviceStatus,
        Task,
        TaskStatus,
        TaskType,
    )
except ImportError:  # запуск как `python panel/manager.py`
    from models import (
        Account,
        AccountStatus,
        Device,
        DeviceStatus,
        Task,
        TaskStatus,
        TaskType,
    )


class FarmManager:
    def __init__(self, session):
        self.session = session

    # --- Accounts ---

    def create_account(self, phone_number):
        account = Account(phone_number=phone_number, status=AccountStatus.NEW)
        self.session.add(account)
        self.session.commit()
        self.session.refresh(account)
        return account

    def list_accounts(self, limit=100):
        return list(
            self.session.scalars(select(Account).order_by(Account.id).limit(limit)).all()
        )

    # --- Devices ---

    def assign_free_device_to_account(self, account_id):
        account = self.session.get(Account, account_id)
        if account is None:
            raise ValueError(f"Account not found: {account_id}")
        device = self.session.scalar(
            select(Device)
            .where(Device.status == DeviceStatus.ONLINE, Device.account_id.is_(None))
            .order_by(Device.id)
            .limit(1)
        )
        if device is None:
            return None
        device.account_id = account.id
        device.status = DeviceStatus.BUSY
        self.session.commit()
        self.session.refresh(device)
        return device

    def release_device(self, device_id):
        device = self.session.get(Device, device_id)
        if device is None:
            return None
        device.account_id = None
        device.status = DeviceStatus.ONLINE
        self.session.commit()
        self.session.refresh(device)
        return device

    def list_devices(self, limit=100):
        return list(
            self.session.scalars(select(Device).order_by(Device.id).limit(limit)).all()
        )

    # --- Tasks ---

    def create_task(self, account_id, task_type, payload):
        account = self.session.get(Account, account_id)
        if account is None:
            raise ValueError(f"Account not found: {account_id}")
        if isinstance(task_type, str):
            task_type = TaskType(task_type)
        task = Task(
            account_id=account.id,
            task_type=task_type,
            payload=dict(payload or {}),
            status=TaskStatus.PENDING,
        )
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def get_next_task_for_device(self, serial):
        """Самая старая PENDING-задача аккаунта, привязанного к устройству."""
        device = self.session.scalar(select(Device).where(Device.serial == serial))
        if device is None or device.account_id is None:
            return None
        return self.session.scalar(
            select(Task)
            .where(
                Task.account_id == device.account_id,
                Task.status == TaskStatus.PENDING,
            )
            .order_by(Task.id)
            .limit(1)
        )

    def update_task_status(self, task_id, status, result=None):
        task = self.session.get(Task, task_id)
        if task is None:
            return None
        task.status = TaskStatus(status) if isinstance(status, str) else status
        if result is not None:
            task.result = result
        self.session.commit()
        self.session.refresh(task)
        return task

    def list_tasks(self, limit=100):
        return list(
            self.session.scalars(select(Task).order_by(Task.id.desc()).limit(limit)).all()
        )
