"""Tests for reference flow usage collector."""

from __future__ import annotations

from pathlib import Path

from tiangong_lca_spec.lci_analysis.upstream.reference_usage import ReferenceFlowUsageCollector


class _FakeRepository:
    def __init__(self) -> None:
        self._records = {
            "proc-a": self._build_record("proc-a", "flow-a"),
            "proc-b": self._build_record("proc-b", "flow-b"),
            "proc-c": self._build_record("proc-c", "flow-a"),
        }

    @staticmethod
    def _build_record(process_uuid: str, flow_uuid: str) -> dict:
        return {
            "json_ordered": {
                "processDataSet": {
                    "processInformation": {
                        "dataSetInformation": {"common:UUID": process_uuid},
                        "quantitativeReference": {"referenceToReferenceFlow": "1"},
                    },
                    "exchanges": {
                        "exchange": [
                            {
                                "@dataSetInternalID": "1",
                                "referenceToFlowDataSet": {"@refObjectId": flow_uuid},
                            }
                        ]
                    },
                }
            }
        }

    def detect_current_user_id(self) -> str:
        return "user-1"

    def list_json_ids(self, user_id: str) -> list[str]:
        assert user_id == "user-1"
        return list(self._records.keys())

    def fetch_record(self, table: str, record_id: str, *, preferred_user_id: str | None = None) -> dict | None:
        assert table == "processes"
        assert preferred_user_id == "user-1"
        return self._records.get(record_id)


def test_reference_flow_usage_collector(tmp_path: Path) -> None:
    collector = ReferenceFlowUsageCollector(_FakeRepository(), export_dir=tmp_path)

    stats = collector.collect(["flow-a", "flow-b", "flow-x"])

    assert set(stats) == {"flow-a", "flow-b"}
    assert stats["flow-a"].process_count == 2
    assert stats["flow-b"].process_count == 1
    exported = tmp_path / "flow-a" / "proc-a.json"
    assert exported.exists()
