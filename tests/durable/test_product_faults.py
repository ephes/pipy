"""Real SQLite header/admission rollback and acknowledgement ambiguity."""

import asyncio
import sqlite3
import threading

import pytest
from generation_support import Wire
from test_product_session import profile

from pipy_harness import durable_product_api as product_api
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable import tx as tx_module
from pipy_harness.native.durable.inspection_read import entries, tasks
from pipy_harness.native.durable.product_binding import header
from pipy_harness.native.durable.tasks import define_task
from pipy_harness.sdk import (
    DurableProductError,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


def persisted(path):
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return {
            table: [
                sqlite._decode(row[0])
                for row in db.execute("SELECT record FROM " + table)
            ]
            for table in ("entries", "tasks", "documents")
        }


def test_real_initializer_failure_rolls_back_entry_and_header_then_one_input(
    tmp_path, monkeypatch
):
    wire = Wire()
    actual = product_api.GENERATION_ADMISSION

    def fail(_):
        raise ValueError("synthetic private initializer failure")

    broken = define_task(actual.name, actual.version, fail, {}, actual.abort)

    async def scenario():
        product = await create_durable_product_session(
            tmp_path / "product.sqlite",
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={},
        )
        try:
            with monkeypatch.context() as patch:
                patch.setattr(product_api, "GENERATION_ADMISSION", broken)
                with pytest.raises(DurableProductError, match="unavailable"):
                    await product.submit("never accepted")
            facts = await product._read(
                lambda: (
                    product._header(),
                    entries(
                        product._session._owner.storage,
                        product._header()["conversation"],
                    ),
                    tasks(product._session._owner.storage),
                )
            )
            assert facts[0]["initial_entry"] is facts[0]["initial_generation"] is None
            assert facts[1] == {} and facts[2] == []
            await product.submit("input")
            assert (await product.resume()).inspection.outcome == "completed"
            assert len(wire.calls) == 1
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 10))


def install_fault(patch, cut, reached, *, admission):
    original = tx_module._TxCore.adopt

    def selected(db):
        table = "tasks" if admission else "documents"
        return db.execute("SELECT count(*) FROM " + table).fetchone()[0] > 0

    def before(db):
        if selected(db):
            reached.set()
            raise sqlite3.OperationalError("synthetic private commit refusal")

    def after(db):
        if selected(db):
            reached.set()
            if cut == "ack":
                raise RuntimeError("synthetic private acknowledgement loss")

    def adopt(self, seq):
        if reached.is_set():
            raise RuntimeError("synthetic private adoption failure")
        return original(self, seq)

    patch.setattr(
        sqlite,
        "_before_commit" if cut == "before" else "_after_commit",
        before if cut == "before" else after,
    )
    if cut == "adoption":
        patch.setattr(tx_module._TxCore, "adopt", adopt)


@pytest.mark.parametrize("cut", ["before", "ack", "adoption"])
def test_header_fault_cannot_promote_uninitialized_store(tmp_path, monkeypatch, cut):
    path = tmp_path / "product.sqlite"
    wire = Wire()
    reached = threading.Event()

    async def scenario():
        with monkeypatch.context() as patch:
            install_fault(patch, cut, reached, admission=False)
            with pytest.raises(Exception):
                await create_durable_product_session(
                    path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
                )
        assert reached.is_set()
        if cut == "before":
            with pytest.raises(DurableProductError, match="uninitialized_product"):
                await open_durable_product_session(path, workspace=tmp_path, tools={})
        else:
            product = await open_durable_product_session(
                path, workspace=tmp_path, tools={}
            )
            assert (await product.inspect()).tasks == ()
            await product.close()
        assert wire.calls == []

    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("cut", ["before", "ack", "adoption"])
def test_atomic_input_fault_reopen_never_resubmits_acknowledged_tuple(
    tmp_path, monkeypatch, cut
):
    path = tmp_path / "product.sqlite"
    wire = Wire()
    reached = threading.Event()

    async def scenario():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        with monkeypatch.context() as patch:
            install_fault(patch, cut, reached, admission=True)
            with pytest.raises(Exception):
                await product.submit("input")
            await product.close()
        assert reached.is_set()
        stored = persisted(path)
        assert (
            len(stored["entries"])
            == len(stored["tasks"])
            == (0 if cut == "before" else 1)
        )
        assert wire.calls == []
        product = await open_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools={}
        )
        try:
            value = await product._read(
                lambda: header(
                    product._session._owner.storage, path.resolve(), tmp_path.resolve()
                )
            )
            if cut == "before":
                assert value["initial_entry"] is value["initial_generation"] is None
                await product.submit("input")
            else:
                assert value["initial_entry"] == stored["entries"][0]["id"]
                assert value["initial_generation"] == stored["tasks"][0]["id"]
                with pytest.raises(DurableProductError, match="already_admitted"):
                    await product.submit("input again")
            assert (await product.resume()).inspection.outcome == "completed"
            assert len(wire.calls) == 1
        finally:
            await product.close()

    asyncio.run(asyncio.wait_for(scenario(), 15))
