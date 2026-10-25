"""Pure P16 relational view and explicit Pi/native target-policy difference.

No credential/material lookup, provider construction, migration or effects occur
here. Only retained synthetic records and public source codecs are consumed.
"""

from __future__ import annotations

import copy

from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.durable.canonical_codec import decode_canonical

IDS = ("unprepared-current", "prepared-current")
FIELDS = {
    "phase",
    "before_requests",
    "committed",
    "input",
    "loaded_phase",
    "model",
    "thinking",
    "cutoff_consistent",
    "session_consistent",
    "unprepared_target",
}
POLICY = "pi-current-model-vs-native-prepared-target-refusal-v1"
MUTANTS = (
    "pre_effect",
    "cutoff",
    "loaded_phase",
    "model",
    "thinking",
    "session",
    "unprepared_target",
    "prepared_success",
)


def native_parts(value, d):
    capture, resumed = value["capture"], value["resumed"]
    before = resumed["before"][0]
    old = capture["task"]
    loaded = next(t for t in before["tasks"] if t["id"] == old["id"])
    d.require(
        d.same(loaded["state"]["checkpoint"], old["state"]["checkpoint"]),
        "P16 native checkpoint changed at open",
    )
    if value["id"] == "prepared-current":
        frame = old["state"]["checkpoint"]
        snapshot = decode_canonical(frame["payload"])
    else:
        entry = next(
            e for e in resumed["final"][0]["entries"] if e["kind"] == "pi.assistant"
        )
        frame = entry["data"]["receipt"]
        snapshot = decode_canonical(entry["data"]["request"])
    d.require(type(snapshot) is AgentProviderRequestSnapshot, "P16 native request DTO")
    return capture, resumed, old, loaded, frame, snapshot


def native_view(value, parent):
    d = parent.d
    capture, resumed, old, loaded, frame, snapshot = native_parts(value, d)
    entry = capture["independent"]["observed"]["stored"]["entry"]
    message = decode_canonical(entry["model"][0])
    prepared = value["id"] == "prepared-current"
    return {
        "phase": old["state"]["checkpoint"]["phase"],
        "before_requests": capture["before_requests"],
        "committed": d.same(old, capture["independent"]["observed"]["stored"]["task"]),
        "input": message.content.value,
        "loaded_phase": loaded["state"]["checkpoint"]["phase"],
        "model": [
            frame["settings"]["provider"],
            frame["settings"]["model"],
            frame["settings"]["api"],
        ],
        "thinking": frame["settings"]["thinking"],
        "cutoff_consistent": frame["identity"]["cutoff"] == capture["entry"]
        if prepared
        else None,
        "session_consistent": bool(snapshot.request.session_id)
        and resumed["final"][0]["provider"]["sessionId"] == snapshot.request.session_id,
        "unprepared_target": None
        if prepared
        else (
            "current"
            if len(resumed["requests"]) == 1
            and resumed["requests"][0]["url"].startswith(
                "https://fixture.invalid/current/"
            )
            else "other"
        ),
    }


def reference_view(value, parent):
    d = parent.d
    capture, resumed = value["capture"], value["resumed"]
    old = capture["task"]
    loaded = next(t for t in resumed["before"] if t["id"] == old["id"])
    request = resumed["requests"][0]
    entry = capture["independent"]["observed"]["stored"]["entry"]
    prepared = value["id"] == "prepared-current"
    d.require(
        d.same(loaded["state"]["checkpoint"], old["state"]["checkpoint"]),
        "P16 reference checkpoint changed at reopen",
    )
    return {
        "phase": old["state"]["checkpoint"]["phase"],
        "before_requests": len(capture["requests"]),
        "committed": d.same(old, capture["independent"]["observed"]["stored"]["task"]),
        "input": entry["model"][0]["content"],
        "loaded_phase": loaded["state"]["checkpoint"]["phase"],
        "model": [request["provider"], request["model"], request["api"]],
        "thinking": request["options"].get("reasoning"),
        "cutoff_consistent": old["state"]["checkpoint"]["cutoff"] == capture["entry"]
        if prepared
        else None,
        "session_consistent": bool(request["options"].get("sessionId"))
        and resumed["provider"]["sessionId"] == request["options"]["sessionId"],
        "unprepared_target": None
        if prepared
        else (
            "current"
            if request["target"] == "https://fixture.invalid/current"
            else "other"
        ),
    }


def policy_valid(reference, native, parent):
    """The prepared-target difference is checked explicitly, never called parity."""
    d = parent.d
    d.require(
        [v["id"] for v in reference] == list(IDS) == [v["id"] for v in native],
        "P16 profile cases",
    )
    for pi, py in zip(reference, native, strict=True):
        if not d.same(reference_view(pi, parent), native_view(py, parent)):
            return False
        resumed = pi["resumed"]
        generation = [t for t in resumed["after"] if t["kind"] == "pi.generation"]
        if not generation or any(
            t["state"]["status"] != "terminal"
            or t["state"]["outcome"]["status"] != "completed"
            for t in generation
        ):
            return False
        if (
            len(resumed["requests"]) != 1
            or resumed["requests"][0]["target"] != "https://fixture.invalid/current"
        ):
            return False
        current = py["resumed"]
        prepared = py["id"] == "prepared-current"
        if current["code"] != int(prepared) or len(current["requests"]) != (
            0 if prepared else 1
        ):
            return False
        if current["output"]["inspection"]["outcome"] != (
            "faulted" if prepared else "completed"
        ):
            return False
    return True


def mutate(native, name):
    result = copy.deepcopy(native)
    unprepared, prepared = result
    if name == "pre_effect":
        prepared["capture"]["before_requests"] += 1
    elif name == "cutoff":
        prepared["capture"]["entry"] += 1
    elif name == "loaded_phase":
        prepared["resumed"]["before"][0]["tasks"][0]["state"]["checkpoint"]["phase"] = (
            "prepare"
        )
    elif name == "model":
        prepared["capture"]["task"]["state"]["checkpoint"]["settings"]["model"] += (
            "-other"
        )
    elif name == "thinking":
        prepared["capture"]["task"]["state"]["checkpoint"]["settings"]["thinking"] = (
            "high"
        )
    elif name == "session":
        prepared["resumed"]["final"][0]["provider"]["sessionId"] += "-other"
    elif name == "unprepared_target":
        unprepared["resumed"]["requests"][0]["url"] = (
            "https://fixture.invalid/original/chat/completions"
        )
    elif name == "prepared_success":
        prepared["resumed"]["code"] = 0
        prepared["resumed"]["output"]["inspection"]["outcome"] = "completed"
    else:
        raise ValueError("Unknown P16 semantic control")
    return result
