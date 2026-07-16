import json
import threading
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from riskagent.database import Base, make_engine
from riskagent.models import RiskRuleDimensionRow, RiskRuleRow, RiskRuleSetRow
from riskagent.risk_rules import RiskRuleCreate, RiskRuleRepository
from riskagent.seed_data import seed_reference_data, verify_risk_rule_seed

SEED_PATH = Path(__file__).parents[1] / "configs" / "hk_ipo_risk_rules_v1.json"
SEED_DATA = json.loads(SEED_PATH.read_text(encoding="utf-8"))
RULE_SET_ID = SEED_DATA["rule_set_id"]


def _session_factory(tmp_path: Path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _counts(session_factory: sessionmaker) -> tuple[int, int]:
    with session_factory() as session:
        rules = session.scalar(
            select(func.count()).select_from(RiskRuleRow).where(RiskRuleRow.rule_set_id == RULE_SET_ID)
        )
        dims = session.scalar(
            select(func.count())
            .select_from(RiskRuleDimensionRow)
            .where(RiskRuleDimensionRow.rule_set_id == RULE_SET_ID)
        )
    return rules or 0, dims or 0


def test_seed_loads_full_rule_catalog(tmp_path):
    session_factory = _session_factory(tmp_path)
    seed_reference_data(session_factory)

    rules, dims = _counts(session_factory)
    assert rules == len(SEED_DATA["rules"]) == 147
    assert dims == len(SEED_DATA["dimensions"]) == 3
    with session_factory() as session:
        categories = set(
            session.scalars(
                select(RiskRuleRow.category).where(RiskRuleRow.rule_set_id == RULE_SET_ID)
            ).all()
        )
    assert categories == {rule["category"] for rule in SEED_DATA["rules"]}
    assert len(categories) == 28


def test_verify_reseeds_partial_rule_set(tmp_path):
    session_factory = _session_factory(tmp_path)
    seed_reference_data(session_factory)

    # Simulate a half-migrated legacy DB: rule-set row exists but rules are gone.
    with session_factory() as session:
        session.query(RiskRuleRow).delete()
        session.commit()
    assert _counts(session_factory)[0] == 0

    seed_reference_data(session_factory)
    rules, dims = _counts(session_factory)
    assert rules == 147
    assert dims == 3


def test_verify_keeps_user_crud_changes(tmp_path):
    session_factory = _session_factory(tmp_path)
    seed_reference_data(session_factory)
    repo = RiskRuleRepository(session_factory=session_factory, ensure_schema=False)
    repo.create_rule(
        RiskRuleCreate(
            dimension_code="D1",
            dimension="维度1：标准化财务指标风险",
            rule_type="quantitative_financial",
            category="测试分类",
            risk_item="用户新增规则",
            description="用户新增",
            trigger_condition="阈值",
            calculation_or_method="方法",
            source_sections="章节",
        )
    )
    assert _counts(session_factory)[0] == 148

    # Re-running the seed must not clobber the user's extra rule.
    seed_reference_data(session_factory)
    assert _counts(session_factory)[0] == 148


def test_verify_reseeds_on_version_bump(tmp_path):
    session_factory = _session_factory(tmp_path)
    seed_reference_data(session_factory)
    with session_factory() as session:
        row = session.get(RiskRuleSetRow, RULE_SET_ID)
        row.version = "v0.9-legacy"
        session.commit()

    with session_factory() as session:
        verify_risk_rule_seed(session)
        session.commit()

    with session_factory() as session:
        row = session.get(RiskRuleSetRow, RULE_SET_ID)
        assert row.version == SEED_DATA.get("version", "v1.1")
    assert _counts(session_factory)[0] == 147


def test_concurrent_create_yields_unique_rule_ids(tmp_path):
    session_factory = _session_factory(tmp_path)
    seed_reference_data(session_factory)
    repo = RiskRuleRepository(session_factory=session_factory, ensure_schema=False)

    created: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def create(index: int) -> None:
        try:
            rule = repo.create_rule(
                RiskRuleCreate(
                    dimension_code="D2",
                    dimension="维度2：非标隐性风险",
                    rule_type="qualitative_disclosure",
                    category="并发测试",
                    risk_item=f"并发规则 {index}",
                    description="并发",
                    trigger_condition="阈值",
                    calculation_or_method="方法",
                    source_sections="章节",
                )
            )
            with lock:
                created.append(rule.rule_id)
        except Exception as exc:  # pragma: no cover - failure detail for assertion
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=create, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(created) == len(set(created)) == 6
    assert _counts(session_factory)[0] == 153
