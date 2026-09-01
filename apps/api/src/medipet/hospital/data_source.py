from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from medipet.capability_files import fake_hospital_data_path
from medipet.hospital.operations import (
    Department,
    Doctor,
    Hospital,
    HospitalDataError,
    HospitalServiceLocation,
    HospitalWayfindingGuidance,
    WayfindingOrigin,
)


@dataclass(frozen=True)
class ScheduleTemplate:
    template_id: str
    doctor_id: str
    day_offsets: tuple[int, ...]
    times: tuple[time, ...]
    duration_minutes: int
    fee_cents: int


@dataclass(frozen=True)
class InitialBooking:
    appointment_id: str
    patient_id: str
    template_id: str
    day_offset: int
    time: time


@dataclass(frozen=True)
class FakeHospitalDataSource:
    hospital: Hospital
    departments: tuple[Department, ...]
    doctors: tuple[Doctor, ...]
    schedules: tuple[ScheduleTemplate, ...]
    initial_bookings: tuple[InitialBooking, ...]
    wayfinding_origins: tuple[WayfindingOrigin, ...]
    service_locations: tuple[HospitalServiceLocation, ...]
    wayfinding_guidance: tuple[HospitalWayfindingGuidance, ...]

    @classmethod
    def load_default(cls, path: Path | None = None) -> FakeHospitalDataSource:
        return cls.load(path or fake_hospital_data_path())

    @classmethod
    def load(cls, path: Path) -> FakeHospitalDataSource:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise HospitalDataError(f"无法加载虚构医院数据: {error}") from error
        if not isinstance(raw, dict):
            raise HospitalDataError("虚构医院数据的根节点必须是对象")
        try:
            return cls._from_raw(raw)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, HospitalDataError):
                raise
            raise HospitalDataError(f"虚构医院数据无效: {error}") from error

    @classmethod
    def _from_raw(cls, raw: dict[str, Any]) -> FakeHospitalDataSource:
        hospital_raw = _object(raw, "hospital")
        hospital = Hospital(
            hospital_id=_text(hospital_raw, "id"),
            name=_text(hospital_raw, "name"),
            timezone=_text(hospital_raw, "timezone"),
        )
        hospital_timezone(hospital.timezone)

        departments = tuple(
            Department(
                department_id=_text(item, "id"),
                name=_text(item, "name"),
                description=_text(item, "description"),
            )
            for item in _object_list(raw, "departments")
        )
        doctors = tuple(
            Doctor(
                doctor_id=_text(item, "id"),
                department_id=_text(item, "department_id"),
                name=_text(item, "name"),
                title=_text(item, "title"),
            )
            for item in _object_list(raw, "doctors")
        )
        schedules = tuple(
            ScheduleTemplate(
                template_id=_text(item, "id"),
                doctor_id=_text(item, "doctor_id"),
                day_offsets=_integer_tuple(item, "day_offsets"),
                times=_time_tuple(item, "times"),
                duration_minutes=_positive_integer(item, "duration_minutes"),
                fee_cents=_positive_integer(item, "fee_cents"),
            )
            for item in _object_list(raw, "schedules")
        )
        initial_bookings = tuple(
            InitialBooking(
                appointment_id=_text(item, "appointment_id"),
                patient_id=_text(item, "patient_id"),
                template_id=_text(item, "template_id"),
                day_offset=_non_negative_integer(item, "day_offset"),
                time=_time_value(item, "time"),
            )
            for item in _object_list(raw, "initial_bookings", required=False)
        )
        wayfinding_raw = raw.get("wayfinding")
        if wayfinding_raw is None:
            origins: tuple[WayfindingOrigin, ...] = ()
            locations: tuple[HospitalServiceLocation, ...] = ()
            guidance: tuple[HospitalWayfindingGuidance, ...] = ()
        else:
            if not isinstance(wayfinding_raw, dict):
                raise HospitalDataError("wayfinding 必须是对象")
            data_version = _text(wayfinding_raw, "data_version")
            origins = tuple(
                WayfindingOrigin(
                    origin_id=_text(item, "id"),
                    display_name=_text(item, "display_name"),
                    aliases=_optional_text_tuple(item, "aliases"),
                )
                for item in _object_list(wayfinding_raw, "origins")
            )
            locations = tuple(
                HospitalServiceLocation(
                    location_id=_text(item, "id"),
                    display_name=_text(item, "display_name"),
                    category=_location_category(item),
                    aliases=_optional_text_tuple(item, "aliases"),
                )
                for item in _object_list(wayfinding_raw, "service_locations")
            )
            origins_by_id = {item.origin_id: item for item in origins}
            locations_by_id = {item.location_id: item for item in locations}
            guidance = tuple(
                _wayfinding_guidance(item, origins_by_id, locations_by_id, data_version)
                for item in _object_list(wayfinding_raw, "guidance")
            )
        source = cls(
            hospital,
            departments,
            doctors,
            schedules,
            initial_bookings,
            origins,
            locations,
            guidance,
        )
        source._validate_references()
        return source

    def _validate_references(self) -> None:
        _require_unique((item.department_id for item in self.departments), "科室 ID")
        _require_unique((item.doctor_id for item in self.doctors), "医生 ID")
        _require_unique((item.template_id for item in self.schedules), "排班模板 ID")
        _require_unique((item.appointment_id for item in self.initial_bookings), "预约 ID")
        _require_unique((item.origin_id for item in self.wayfinding_origins), "方位指引起点 ID")
        _require_unique((item.location_id for item in self.service_locations), "院内服务地点 ID")
        _require_unique(
            (
                alias
                for item in self.wayfinding_origins
                for alias in (item.display_name, *item.aliases)
            ),
            "方位指引起点名称或别名",
        )
        _require_unique(
            (
                alias
                for item in self.service_locations
                for alias in (item.display_name, *item.aliases)
            ),
            "院内服务地点名称或别名",
        )
        department_ids = {item.department_id for item in self.departments}
        doctor_ids = {item.doctor_id for item in self.doctors}
        templates = {item.template_id: item for item in self.schedules}
        if not self.departments or not self.doctors or not self.schedules:
            raise HospitalDataError("虚构医院必须包含科室、医生和排班")
        if any(doctor.department_id not in department_ids for doctor in self.doctors):
            raise HospitalDataError("医生引用了不存在的科室")
        if any(schedule.doctor_id not in doctor_ids for schedule in self.schedules):
            raise HospitalDataError("排班引用了不存在的医生")
        covered_department_ids = {doctor.department_id for doctor in self.doctors}
        if covered_department_ids != department_ids:
            raise HospitalDataError("每个科室必须至少配置一名医生")
        scheduled_doctor_ids = {schedule.doctor_id for schedule in self.schedules}
        if scheduled_doctor_ids != doctor_ids:
            raise HospitalDataError("每名医生必须至少配置一个排班")
        for schedule in self.schedules:
            if len(schedule.day_offsets) != len(set(schedule.day_offsets)):
                raise HospitalDataError("排班日期偏移不能重复")
            if len(schedule.times) != len(set(schedule.times)):
                raise HospitalDataError("排班时间不能重复")
            if any(offset < 0 or offset > 14 for offset in schedule.day_offsets):
                raise HospitalDataError("排班日期偏移必须位于未来两周内")
            if not schedule.day_offsets or not schedule.times:
                raise HospitalDataError("排班必须包含日期偏移和时间")
        for booking in self.initial_bookings:
            schedule = templates.get(booking.template_id)
            if schedule is None:
                raise HospitalDataError("初始预约引用了不存在的排班模板")
            if booking.day_offset not in schedule.day_offsets or booking.time not in schedule.times:
                raise HospitalDataError("初始预约没有对应号源")
        occupied_slots = [
            (booking.template_id, booking.day_offset, booking.time)
            for booking in self.initial_bookings
        ]
        if len(occupied_slots) != len(set(occupied_slots)):
            raise HospitalDataError("初始预约不能重复占用同一号源")
        combinations = [
            (item.origin.origin_id, item.destination.location_id, item.mode)
            for item in self.wayfinding_guidance
        ]
        if len(combinations) != len(set(combinations)):
            raise HospitalDataError("方位指引组合不能重复")


def _wayfinding_guidance(
    raw: dict[str, Any],
    origins: dict[str, WayfindingOrigin],
    locations: dict[str, HospitalServiceLocation],
    data_version: str,
) -> HospitalWayfindingGuidance:
    origin_id = _text(raw, "origin_id")
    destination_id = _text(raw, "destination_id")
    try:
        origin = origins[origin_id]
    except KeyError:
        raise HospitalDataError("方位指引引用了不存在的起点") from None
    try:
        destination = locations[destination_id]
    except KeyError:
        raise HospitalDataError("方位指引引用了不存在的服务地点") from None
    mode = _text(raw, "mode")
    if mode not in {"standard", "accessible"}:
        raise HospitalDataError("方位指引模式必须是 standard 或 accessible")
    steps = _text_tuple(raw, "steps")
    if not steps:
        raise HospitalDataError("方位指引步骤不能为空")
    return HospitalWayfindingGuidance(
        origin=origin,
        destination=destination,
        mode=cast(Literal["standard", "accessible"], mode),
        steps=steps,
        notice=_optional_text(raw, "notice"),
        data_version=data_version,
    )


def _location_category(raw: dict[str, Any]) -> Literal["department", "service"]:
    value = _text(raw, "category")
    if value not in {"department", "service"}:
        raise HospitalDataError("院内服务地点类别必须是 department 或 service")
    return cast(Literal["department", "service"], value)


def _object(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw[name]
    if not isinstance(value, dict):
        raise HospitalDataError(f"{name} 必须是对象")
    return value


def _object_list(
    raw: dict[str, Any], name: str, *, required: bool = True
) -> list[dict[str, Any]]:
    value = raw[name] if required else raw.get(name, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HospitalDataError(f"{name} 必须是对象数组")
    return value


def _text(raw: dict[str, Any], name: str) -> str:
    value = raw[name]
    if not isinstance(value, str) or not value.strip():
        raise HospitalDataError(f"{name} 必须是非空文本")
    return value.strip()


def _optional_text(raw: dict[str, Any], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    return _text({name: value}, name)


def _text_tuple(raw: dict[str, Any], name: str) -> tuple[str, ...]:
    value = raw[name]
    if not isinstance(value, list):
        raise HospitalDataError(f"{name} 必须是文本数组")
    return tuple(_text({name: item}, name) for item in value)


def _optional_text_tuple(raw: dict[str, Any], name: str) -> tuple[str, ...]:
    return () if name not in raw else _text_tuple(raw, name)


def _integer_tuple(raw: dict[str, Any], name: str) -> tuple[int, ...]:
    value = raw[name]
    if not isinstance(value, list) or any(
        not isinstance(item, int) or isinstance(item, bool) for item in value
    ):
        raise HospitalDataError(f"{name} 必须是整数数组")
    return tuple(value)


def _positive_integer(raw: dict[str, Any], name: str) -> int:
    value = raw[name]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise HospitalDataError(f"{name} 必须是正整数")
    return value


def _non_negative_integer(raw: dict[str, Any], name: str) -> int:
    value = raw[name]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HospitalDataError(f"{name} 必须是非负整数")
    return value


def _parse_time(value: str) -> time:
    pieces = value.split(":")
    if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
        raise HospitalDataError("排班时间必须使用 HH:MM")
    hour, minute = (int(piece) for piece in pieces)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise HospitalDataError("排班时间无效")
    return time(hour, minute)


def _time_value(raw: dict[str, Any], name: str) -> time:
    return _parse_time(_text(raw, name))


def _time_tuple(raw: dict[str, Any], name: str) -> tuple[time, ...]:
    value = raw[name]
    if not isinstance(value, list):
        raise HospitalDataError(f"{name} 必须是时间数组")
    return tuple(_time_value({name: item}, name) for item in value)


def hospital_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name=name)
        raise HospitalDataError("服务医院时区无效") from None


def _require_unique(values: Iterable[str], label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise HospitalDataError(f"{label} 必须唯一")
