from medipet.hospital.selection import resolve_wayfinding_selection


def test_resolves_single_turn_canonical_wayfinding_selection() -> None:
    selection = resolve_wayfinding_selection(
        ("我在门诊楼一层主入口，需要无障碍指引前往儿科门诊。",)
    )

    assert selection is not None
    assert selection.tool_name == "hospital_get_wayfinding_guidance"
    assert selection.arguments == (
        ("destination_id", "location-pediatrics"),
        ("mode", "accessible"),
        ("origin_id", "origin-main-entrance"),
    )


def test_resolves_multi_turn_selection_with_common_aliases() -> None:
    selection = resolve_wayfinding_selection(
        (
            "我在主入口，想去儿科。",
            "无障碍",
        )
    )

    assert selection is not None
    assert dict(selection.arguments) == {
        "origin_id": "origin-main-entrance",
        "destination_id": "location-pediatrics",
        "mode": "accessible",
    }


def test_resolves_a_common_explicit_question_without_a_fixed_sentence_template() -> None:
    selection = resolve_wayfinding_selection(
        ("我在主入口，怎么去儿科？普通路线",)
    )

    assert selection is not None
    assert dict(selection.arguments)["mode"] == "standard"


def test_latest_explicit_mode_overrides_the_mode_in_the_route_message() -> None:
    accessible = resolve_wayfinding_selection(
        ("我在主入口，需要普通指引前往儿科。", "改成无障碍")
    )
    standard = resolve_wayfinding_selection(
        ("我在主入口，需要无障碍指引前往儿科。", "改成普通")
    )

    assert accessible is not None
    assert dict(accessible.arguments)["mode"] == "accessible"
    assert standard is not None
    assert dict(standard.arguments)["mode"] == "standard"


def test_rejects_negated_or_ambiguous_selection_text() -> None:
    for message in (
        "我不在门诊楼一层主入口，也不是要去儿科门诊，我需要无障碍帮助。",
        "我并非想从主入口到儿科，也无需无障碍路线。",
        "我不想从主入口到儿科，普通路线不用查。",
    ):
        assert resolve_wayfinding_selection((message,)) is None
    assert (
        resolve_wayfinding_selection(("我在主入口，想去儿科或门诊药房。", "普通"))
        is None
    )


def test_a_later_negation_invalidates_an_earlier_selection() -> None:
    assert (
        resolve_wayfinding_selection(
            (
                "我在主入口，想去儿科。",
                "普通",
                "不是儿科，我还没确定目的地。",
            )
        )
        is None
    )
