"""Python-only exact typed-token ingress; TS overload errors are not Node claims."""

import dataclasses

import pytest

from pipy_harness.native.durable.documents import (
    CheckpointInfo,
    Doc,
    DocFamily,
    define_doc,
    define_doc_family,
    resolve,
)
from pipy_harness.native.durable.records import OMITTED


@pytest.mark.parametrize("family", [False, True])
@pytest.mark.parametrize("scope", ["session", "conversation", "task"])
def test_immutable_typed_tokens_and_exact_resolution(family, scope):
    factory = define_doc_family if family else define_doc
    kwargs = {"history": "latest", "fork": "initial"} if scope == "conversation" else {}
    spec = factory("kind", 1, scope, dict, **kwargs)
    assert type(spec) is (DocFamily if family else Doc)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.version = 2
    owner = (
        {}
        if scope == "session"
        else {"conversation_id" if scope == "conversation" else "task_id": 7}
    )
    identity, value = resolve(spec, **owner, **({"key": ""} if family else {}))
    assert identity[-1] == (b"" if family else None)
    assert ("key" in value.to_tree()) is family


@pytest.mark.parametrize("factory", [define_doc, define_doc_family])
@pytest.mark.parametrize("version", [True, 0, -1, 1.0, 2**53])
def test_definition_version_requires_exact_positive_safe_integer(factory, version):
    with pytest.raises(TypeError):
        factory("k", version, "session", dict)


@pytest.mark.parametrize("factory", [define_doc, define_doc_family])
@pytest.mark.parametrize("scope", ["session", "task"])
def test_nonconversation_policies_are_rejected(factory, scope):
    for extra in ({"history": "latest"}, {"fork": "initial"}):
        with pytest.raises(TypeError):
            factory("k", 1, scope, dict, **extra)


def test_all_foreign_address_components_are_rejected_without_hooks():
    seen = []

    class Text(str):
        def __hash__(self):
            seen.append("hash")
            return str.__hash__(self)

        def __eq__(self, other):
            seen.append("eq")
            return str.__eq__(self, other)

    class Number(int):
        def __index__(self):
            seen.append("index")
            return int(self)

    for scope in ("session", "conversation", "task"):
        kwargs = (
            {"history": "latest", "fork": "initial"} if scope == "conversation" else {}
        )
        for factory in (define_doc, define_doc_family):
            with pytest.raises(TypeError):
                factory(Text("kind"), 1, scope, dict, **kwargs)
            with pytest.raises(TypeError):
                factory("kind", 1, Text(scope), dict, **kwargs)
    single = define_doc("k", 1, "session", dict)
    family = define_doc_family("k", 1, "session", dict)
    for options in ({"key": Text("key")}, {"key": object()}, {"key": OMITTED}):
        with pytest.raises(TypeError):
            resolve(family, **options)
    with pytest.raises(TypeError):
        resolve(single, key="")
    with pytest.raises(TypeError):
        resolve(define_doc("t", 1, "task", dict), task_id=Number(7))
    with pytest.raises(TypeError):
        resolve(single, task_id=7)
    object.__setattr__(single, "kind", Text("tampered"))
    with pytest.raises(TypeError):
        resolve(single)
    assert seen == []


def test_family_keys_and_kinds_have_exact_utf16_identity():
    one = define_doc_family("\U0001f600", 1, "session", dict)
    two = define_doc_family("\ud83d\ude00", 1, "session", dict)
    assert resolve(one, key="\U0001f600")[0] == resolve(two, key="\ud83d\ude00")[0]
    assert resolve(one, key="")[0] != resolve(one, key="\ud800")[0]


@pytest.mark.parametrize("factory", [define_doc, define_doc_family])
@pytest.mark.parametrize("scope", ["session", "conversation", "task"])
def test_checkpoint_policy_ingress_identity_positional_and_tampered_resolve(
    factory, scope
):
    calls = []

    class Policy:
        def __call__(self, value, ops, info):
            return False

        def __bool__(self):
            calls.append("bool")
            raise AssertionError

    class Foreign:
        def __getattribute__(self, name):
            calls.append(name)
            raise AssertionError

    policy = Policy()
    history, fork = ("latest", "initial") if scope == "conversation" else (None, None)
    spec = factory("k", 1, scope, dict, history, fork, policy)
    assert spec.checkpoint_when is policy
    options = {} if scope == "session" else {scope + "_id": 7}
    if factory is define_doc_family:
        options["key"] = ""
    resolve(spec, **options)
    assert factory("k", 1, scope, dict, history, fork).checkpoint_when is None
    for invalid in (False, 1, Foreign()):
        with pytest.raises(TypeError, match="checkpoint_when"):
            factory("k", 1, scope, dict, history, fork, invalid)
        object.__setattr__(spec, "checkpoint_when", invalid)
        with pytest.raises(TypeError, match="checkpoint_when"):
            resolve(spec, **options)
    assert calls == []
    info = CheckpointInfo(4)
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.deltas_since_base = 5
