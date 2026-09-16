"""MediPet 医院业务记录，复用调用方提供的 Redis 客户端。"""

from redis.asyncio import Redis

from pydantic import TypeAdapter
from hospital.models import AppointmentProposal, AppointmentRecord, Identifier, Slot

_IDENTIFIER = TypeAdapter(Identifier)


class HospitalStore:
    def __init__(self, redis_client: Redis, prefix: str = "medipet:"):
        self.redis = redis_client
        if not prefix:
            raise ValueError("医院存储必须使用非空 Redis 前缀")
        self.prefix = prefix if prefix.endswith(":") else f"{prefix}:"

    def slot_key(self, slot_id: str) -> str:
        return f"{self.prefix}slot:{_IDENTIFIER.validate_python(slot_id)}"

    def proposal_key(self, proposal_id: str) -> str:
        return f"{self.prefix}proposal:{_IDENTIFIER.validate_python(proposal_id)}"

    def appointment_key(self, appointment_id: str) -> str:
        return f"{self.prefix}appointment:{_IDENTIFIER.validate_python(appointment_id)}"

    def appointments_key(self, user_id: str, patient_id: str) -> str:
        return f"{self.prefix}appointments:{_IDENTIFIER.validate_python(user_id)}:{_IDENTIFIER.validate_python(patient_id)}"

    async def get_proposal(self, proposal_id: str) -> AppointmentProposal | None:
        raw = await self.redis.get(self.proposal_key(proposal_id))
        return AppointmentProposal.model_validate_json(raw) if raw is not None else None

    async def get_appointment(self, appointment_id: str) -> AppointmentRecord | None:
        raw = await self.redis.get(self.appointment_key(appointment_id))
        return AppointmentRecord.model_validate_json(raw) if raw is not None else None

    async def get_appointments(self, user_id: str, patient_id: str) -> list[AppointmentRecord]:
        ids = await self.redis.smembers(self.appointments_key(user_id, patient_id))
        if not ids:
            return []
        values = await self.redis.mget([self.appointment_key(item.decode() if isinstance(item, bytes) else item) for item in ids])
        return [AppointmentRecord.model_validate_json(raw) for raw in values if raw is not None]

    async def ensure_slots(self, slots: list[Slot]) -> int:
        """仅补充缺失记录；SET NX 保留已有库存，不为业务记录设置 TTL。"""
        if not slots:
            return 0
        pipeline = self.redis.pipeline(transaction=False)
        for slot in slots:
            pipeline.set(self.slot_key(slot.slot_id), slot.model_dump_json(), nx=True)
        results = await pipeline.execute()
        return sum(bool(item) for item in results)

    async def get_slot(self, slot_id: str) -> Slot | None:
        raw = await self.redis.get(self.slot_key(slot_id))
        return Slot.model_validate_json(raw) if raw is not None else None

    async def get_slots(self, slot_ids: list[str]) -> list[Slot]:
        if not slot_ids:
            return []
        values = await self.redis.mget([self.slot_key(slot_id) for slot_id in slot_ids])
        return [Slot.model_validate_json(raw) for raw in values if raw is not None]
