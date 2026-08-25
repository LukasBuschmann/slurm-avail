from __future__ import annotations

from pathlib import Path

from slurm_avail.config import SETTING_FIELDS, AppConfig, ClusterConfig
from slurm_avail.config_views import (
    CLUSTER_FIELDS,
    ClusterField,
    cluster_editor_table,
    config_table,
    cycle_cluster_field,
    set_cluster_field,
    visible_cluster_fields,
)


def field(key: str) -> ClusterField:
    return next(item for item in CLUSTER_FIELDS if item.key == key)


def row_text(row: list[tuple[str, str]]) -> str:
    return "".join(text for text, _style in row)


def test_cluster_editor_only_shows_relevant_connection_fields() -> None:
    cluster = ClusterConfig(
        name="REMOTE",
        addresses=["login.example.org"],
        authentication="batch",
    )

    assert "addresses" in {item.key for item in visible_cluster_fields(cluster)}
    cycle_cluster_field(cluster, field("authentication"), 1)

    assert cluster.authentication == "interactive"

    cycle_cluster_field(cluster, field("mode"), 1)

    assert cluster.mode == "local"
    assert "addresses" not in {item.key for item in visible_cluster_fields(cluster)}
    assert "authentication" not in {
        item.key for item in visible_cluster_fields(cluster)
    }


def test_authentication_choices_use_plain_labels() -> None:
    cluster = ClusterConfig(
        name="REMOTE",
        addresses=["login.example.org"],
        authentication="batch",
    )
    fields = visible_cluster_fields(cluster)
    selection = next(
        index for index, item in enumerate(fields) if item.key == "authentication"
    )

    _headers, body, selected_body_index = cluster_editor_table(
        cluster,
        Path("config.toml"),
        selection,
        dirty=False,
        message="",
    )

    selected_text = row_text(body[selected_body_index])
    assert selected_text.startswith("> SSH login method")
    assert "[SSH KEY]  Password" in selected_text
    assert "batch" not in selected_text
    assert "interactive" not in selected_text


def test_choice_fields_cycle_without_text_input() -> None:
    cluster = ClusterConfig(name="REMOTE", focus="auto", hidden=False)

    cycle_cluster_field(cluster, field("focus"), 1)
    cycle_cluster_field(cluster, field("hidden"), -1)

    assert cluster.focus == "cpu"
    assert cluster.hidden is True


def test_cluster_text_fields_parse_lists_and_optional_values() -> None:
    cluster = ClusterConfig(name="REMOTE")

    set_cluster_field(cluster, field("addresses"), " login1, login2 ,, ")
    set_cluster_field(cluster, field("user"), "")

    assert cluster.addresses == ["login1", "login2"]
    assert cluster.user is None


def test_cluster_editor_renders_all_choices_and_selected_field() -> None:
    cluster = ClusterConfig(
        name="REMOTE",
        addresses=["login.example.org"],
        focus="gpu",
    )
    fields = visible_cluster_fields(cluster)
    selection = next(index for index, item in enumerate(fields) if item.key == "focus")

    _headers, body, selected_body_index = cluster_editor_table(
        cluster,
        Path("config.toml"),
        selection,
        dirty=True,
        message="",
    )

    selected_text = row_text(body[selected_body_index])
    assert selected_text.startswith("> Resource focus")
    assert "auto  cpu  [GPU]" in selected_text
    assert "Press s to save and apply immediately." in {row_text(row) for row in body}


def test_global_boolean_setting_renders_as_a_choice() -> None:
    config = AppConfig(clusters=[ClusterConfig(name="LOCAL", mode="local")])

    _headers, body, selected_body_index = config_table(
        config,
        Path("config.toml"),
        selection=len(SETTING_FIELDS),
        dirty=False,
        message="",
    )

    selected_text = row_text(body[selected_body_index])
    assert "Startup authentication" in selected_text
    assert "[ON]  off" in selected_text
