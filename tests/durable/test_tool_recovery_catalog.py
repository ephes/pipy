"""Pure catalog binding preserves saved request members and authorization."""

from dataclasses import replace

import pytest
from generation_support import seed
from test_tool_execution import KeyedTool

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.canonical_codec import encode_canonical
from pipy_harness.native.durable.recovery_catalog import RecoveryCatalog
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)


def support(root, names, *, revision="1"):
    ports = [KeyedTool(name, []) for name in names]
    task = define_tool_task(
        {
            p.definition.name: ToolRegistration(
                p, "fixture." + p.definition.name, revision
            )
            for p in ports
        },
        workspace_root=root,
    )
    return task.support.to_tree(), tuple(p.definition for p in ports)


def binding(root, current_names, *, revision="1"):
    saved, offered = support(root, ("one", "two"))
    current, _ = support(root, current_names, revision=revision)
    snapshot = snapshot_provider_request(
        replace(seed(root).request, available_tools=offered)
    )
    header = {"tools": saved, "conversation": 1}
    return RecoveryCatalog.capture(header, current), header, snapshot


def test_same_catalog_retains_original_snapshot_and_members(tmp_path):
    catalog, header, snapshot = binding(tmp_path, ("one", "two"))
    assert catalog.unchanged
    assert catalog.next_snapshot(header, 1, snapshot) is snapshot


@pytest.mark.parametrize(
    "names,revision,expected",
    [
        (("two", "extra"), "1", ("two",)),
        ((), "1", ()),
        (("one", "two"), "2", ()),
    ],
)
def test_narrowed_catalog_keeps_saved_members_and_closes_authorization(
    tmp_path, names, revision, expected
):
    catalog, header, snapshot = binding(tmp_path, names, revision=revision)
    result = catalog.next_snapshot(header, 1, snapshot)
    assert result.advertised_tool_names == expected
    assert tuple(t.name for t in result.request.available_tools) == expected
    for member in result.request.available_tools:
        assert member is next(
            t for t in snapshot.request.available_tools if t.name == member.name
        )
    assert result.request.hidden_tool_names == tuple(
        n for n in ("one", "two") if n not in expected
    )
    assert snapshot.advertised_tool_names == ("one", "two")
    assert result.request.messages == snapshot.request.messages


@pytest.mark.parametrize("mutation", ["header", "conversation", "foreign_offer"])
def test_actual_header_and_selected_round_are_revalidated(tmp_path, mutation):
    catalog, header, snapshot = binding(tmp_path, ("two",))
    cid = 1
    if mutation == "header":
        header = {**header, "unexpected": True}
    elif mutation == "conversation":
        cid = 2
    else:
        _, foreign = support(tmp_path, ("foreign",))
        snapshot = snapshot_provider_request(
            replace(snapshot.request, available_tools=foreign)
        )
    with pytest.raises(ValueError):
        catalog.next_snapshot(header, cid, snapshot)


def identity_binding(root):
    catalog, header, snapshot = binding(root, ("one", "two"))
    # Pure identity controls; full product header/graph admission is separately
    # covered by the actual SQLite product-session and fence tests.
    header.update(
        version=1,
        path=str(root / "product.sqlite"),
        workspace=str(root),
        selection=["openai", "gpt-test", "openai-completions"],
        template=encode_canonical(snapshot),
        initial_entry=None,
        initial_generation=None,
    )
    return RecoveryCatalog.capture(header, catalog.current.thaw()), header, snapshot


def test_admission_pointers_do_not_change_immutable_catalog_identity(tmp_path):
    catalog, header, snapshot = identity_binding(tmp_path)
    admitted = {**header, "initial_entry": 4, "initial_generation": 5}
    assert RecoveryCatalog.capture(admitted, catalog.current.thaw()) == catalog
    assert catalog.next_snapshot(admitted, 1, snapshot) is snapshot
    assert header["initial_entry"] is None and header["initial_generation"] is None


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "path",
        "workspace",
        "conversation",
        "selection",
        "tools",
        "template",
        "unexpected",
    ],
)
def test_every_other_header_field_remains_bound(tmp_path, field):
    catalog, header, snapshot = identity_binding(tmp_path)
    changed = {**header, field: {"different": True}}
    with pytest.raises(ValueError):
        catalog.next_snapshot(changed, 1, snapshot)
