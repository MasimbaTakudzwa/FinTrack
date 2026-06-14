from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sidecar.db.engine import session_scope
from sidecar.db.models import (
    AlertDirection,
    AlertMetric,
    Article,
    ArticleAsset,
    Asset,
    AssetType,
    PriceAlert,
    PricePoint,
)
from sidecar.services import alerts as svc


def _add_article(
    asset_id: int,
    *,
    sentiment: float | None,
    headline: str = "x",
    days_ago: int = 0,
    url: str | None = None,
) -> int:
    with session_scope() as s:
        article = Article(
            url=url or f"https://test/{asset_id}/{datetime.now(UTC).timestamp()}/{sentiment}",
            headline=headline,
            source="Test",
            published_at=datetime.now(UTC) - timedelta(days=days_ago),
            sentiment=sentiment,
        )
        s.add(article)
        s.flush()
        s.add(ArticleAsset(article_id=article.id, asset_id=asset_id))
        return article.id


def _seed_asset(symbol: str = "AAPL", name: str = "Apple Inc.") -> int:
    with session_scope() as s:
        a = Asset(symbol=symbol, name=name, asset_type=AssetType.STOCK)
        s.add(a)
        s.flush()
        return a.id


def _add_price(asset_id: int, close: Decimal, *, ts: datetime | None = None) -> None:
    with session_scope() as s:
        s.add(
            PricePoint(
                asset_id=asset_id,
                timestamp=ts or datetime.now(UTC),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=0,
            )
        )


# ---------------------------------------------------------------------------
# create + validation
# ---------------------------------------------------------------------------


def test_create_alert_happy_path(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold="150.00", direction="above")
    assert a.asset_id == aid
    assert a.symbol == "AAPL"
    assert a.threshold == Decimal("150.00")
    assert a.direction == AlertDirection.ABOVE
    assert a.is_active is True
    assert a.triggered_at is None
    assert a.notified_at is None
    assert a.note is None


def test_create_alert_accepts_decimal_and_float(isolated_db: Path) -> None:
    aid = _seed_asset()
    a1 = svc.create_alert(asset_id=aid, threshold=Decimal("1.5"), direction="below")
    a2 = svc.create_alert(asset_id=aid, threshold=2.25, direction="below")
    assert a1.threshold == Decimal("1.5")
    assert a2.threshold == Decimal("2.25")


def test_create_alert_rejects_nonpositive_threshold(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError):
        svc.create_alert(asset_id=aid, threshold=0, direction="above")
    with pytest.raises(svc.AlertError):
        svc.create_alert(asset_id=aid, threshold=-5, direction="above")


def test_create_alert_rejects_unparseable_threshold(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError):
        svc.create_alert(asset_id=aid, threshold="not-a-number", direction="above")


def test_create_alert_rejects_bad_direction(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError):
        svc.create_alert(asset_id=aid, threshold=1, direction="sideways")


def test_create_alert_unknown_asset(isolated_db: Path) -> None:
    with pytest.raises(svc.AssetNotFoundError):
        svc.create_alert(asset_id=9999, threshold=10, direction="above")


def test_create_alert_rejects_already_crossed_above(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("200.00"))
    with pytest.raises(svc.AlreadyCrossedError):
        svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")


def test_create_alert_rejects_already_crossed_below(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("90.00"))
    with pytest.raises(svc.AlreadyCrossedError):
        svc.create_alert(asset_id=aid, threshold=Decimal("100.00"), direction="below")


def test_create_alert_allows_not_yet_crossed_with_price(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("140.00"))
    # 140 < 150, so an "above 150" alert is a legitimate not-yet-crossed alert.
    a = svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    assert a.triggered_at is None


def test_create_sentiment_alert_not_rejected_when_already_crossed(
    isolated_db: Path,
) -> None:
    """The already-crossed guard is PRICE-only — a sentiment alert whose
    current value already satisfies the threshold must still be created."""
    aid = _seed_asset()
    _add_price(aid, Decimal("200.00"))  # price present, but irrelevant here
    _add_article(aid, sentiment=0.9)
    _add_article(aid, sentiment=0.9, url="https://test/2")
    # Mean sentiment ≈ 0.9 already above 0.4 — must NOT raise AlreadyCrossedError.
    a = svc.create_alert(
        asset_id=aid,
        threshold=Decimal("0.4"),
        direction="above",
        metric="sentiment",
        window_days=7,
    )
    assert a.metric == AlertMetric.SENTIMENT


def test_create_alert_note_too_long(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError):
        svc.create_alert(
            asset_id=aid, threshold=1, direction="above", note="x" * 257
        )


def test_create_alert_strips_and_clears_blank_note(isolated_db: Path) -> None:
    aid = _seed_asset()
    a1 = svc.create_alert(
        asset_id=aid, threshold=1, direction="above", note="  earnings week  "
    )
    assert a1.note == "earnings week"
    a2 = svc.create_alert(
        asset_id=aid, threshold=2, direction="above", note="   "
    )
    assert a2.note is None


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


def test_list_alerts_newest_first(isolated_db: Path) -> None:
    aid = _seed_asset()
    a_old = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    a_new = svc.create_alert(asset_id=aid, threshold=2, direction="above")
    listed = svc.list_alerts()
    assert [a.id for a in listed] == [a_new.id, a_old.id]


def test_list_alerts_filter_by_asset(isolated_db: Path) -> None:
    aid1 = _seed_asset("AAPL", "Apple")
    aid2 = _seed_asset("MSFT", "Microsoft")
    svc.create_alert(asset_id=aid1, threshold=1, direction="above")
    svc.create_alert(asset_id=aid2, threshold=2, direction="above")
    listed = svc.list_alerts(asset_id=aid1)
    assert [a.asset_id for a in listed] == [aid1]


def test_list_alerts_active_only(isolated_db: Path) -> None:
    aid = _seed_asset()
    on = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    off = svc.create_alert(asset_id=aid, threshold=2, direction="above")
    svc.update_alert(off.id, is_active=False)
    listed = svc.list_alerts(active_only=True)
    assert [a.id for a in listed] == [on.id]


def test_list_alerts_hydrates_latest_price(isolated_db: Path) -> None:
    aid = _seed_asset()
    # Create below the eventual price (not yet crossed), then add the price.
    svc.create_alert(asset_id=aid, threshold=1, direction="below")
    _add_price(aid, Decimal("123.45"))
    listed = svc.list_alerts()
    assert listed[0].last_price == Decimal("123.45")
    assert listed[0].last_price_at is not None


def test_list_alerts_last_price_null_when_no_bars(isolated_db: Path) -> None:
    aid = _seed_asset()
    svc.create_alert(asset_id=aid, threshold=1, direction="above")
    listed = svc.list_alerts()
    assert listed[0].last_price is None
    assert listed[0].last_price_at is None


def test_get_alert(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    got = svc.get_alert(a.id)
    assert got.id == a.id


def test_get_alert_not_found(isolated_db: Path) -> None:
    with pytest.raises(svc.AlertNotFoundError):
        svc.get_alert(9999)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_alert_fields(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    u = svc.update_alert(
        a.id, threshold=Decimal("99.99"), direction="below", is_active=False
    )
    assert u.threshold == Decimal("99.99")
    assert u.direction == AlertDirection.BELOW
    assert u.is_active is False


def test_update_alert_reset_clears_timestamps(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    _add_price(aid, Decimal("200.00"))
    fired = svc.check_alerts()
    assert fired == 1
    got = svc.get_alert(a.id)
    assert got.triggered_at is not None

    svc.mark_notified(a.id)
    got = svc.get_alert(a.id)
    assert got.notified_at is not None

    u = svc.update_alert(a.id, reset=True)
    assert u.triggered_at is None
    assert u.notified_at is None


def test_update_alert_note_only_applied_when_flagged(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(
        asset_id=aid, threshold=1, direction="above", note="initial"
    )
    # Omitting update_note → note untouched.
    u = svc.update_alert(a.id, threshold=Decimal("2"))
    assert u.note == "initial"
    # Clear explicitly.
    u = svc.update_alert(a.id, note=None, update_note=True)
    assert u.note is None
    # Set again.
    u = svc.update_alert(a.id, note="reset", update_note=True)
    assert u.note == "reset"


def test_update_alert_note_too_long(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with pytest.raises(svc.AlertError):
        svc.update_alert(a.id, note="x" * 257, update_note=True)


def test_update_alert_rejects_nonpositive_threshold(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with pytest.raises(svc.AlertError):
        svc.update_alert(a.id, threshold=0)


def test_update_alert_not_found(isolated_db: Path) -> None:
    with pytest.raises(svc.AlertNotFoundError):
        svc.update_alert(9999, threshold=1)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_alert(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    svc.delete_alert(a.id)
    assert svc.list_alerts() == []


def test_delete_alert_not_found(isolated_db: Path) -> None:
    with pytest.raises(svc.AlertNotFoundError):
        svc.delete_alert(9999)


def test_delete_asset_cascades_to_alerts(isolated_db: Path) -> None:
    aid = _seed_asset()
    svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with session_scope() as s:
        asset = s.get(Asset, aid)
        assert asset is not None
        s.delete(asset)
    with session_scope() as s:
        assert s.query(PriceAlert).count() == 0


# ---------------------------------------------------------------------------
# check_alerts — the crossing detector
# ---------------------------------------------------------------------------


def test_check_alerts_above_fires_when_close_hits_threshold(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    _add_price(aid, Decimal("200.00"))
    assert svc.check_alerts() == 1
    got = svc.get_alert(a.id)
    assert got.triggered_at is not None
    # Second pass is a no-op (already fired).
    assert svc.check_alerts() == 0


def test_check_alerts_above_does_not_fire_when_under(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("140.00"))
    svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    assert svc.check_alerts() == 0


def test_check_alerts_below_fires_when_close_hits_threshold(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=Decimal("100.00"), direction="below")
    _add_price(aid, Decimal("90.00"))
    assert svc.check_alerts() == 1
    got = svc.get_alert(a.id)
    assert got.triggered_at is not None


def test_check_alerts_below_does_not_fire_when_over(isolated_db: Path) -> None:
    aid = _seed_asset()
    _add_price(aid, Decimal("110.00"))
    svc.create_alert(asset_id=aid, threshold=Decimal("100.00"), direction="below")
    assert svc.check_alerts() == 0


def test_check_alerts_equal_threshold_fires(isolated_db: Path) -> None:
    aid = _seed_asset()
    # above: close >= threshold (inclusive). Create first (no price), then the
    # bar that lands exactly on the threshold.
    svc.create_alert(asset_id=aid, threshold=Decimal("100.00"), direction="above")
    _add_price(aid, Decimal("100.00"))
    assert svc.check_alerts() == 1


def test_check_alerts_skips_inactive(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    _add_price(aid, Decimal("200.00"))
    svc.update_alert(a.id, is_active=False)
    assert svc.check_alerts() == 0


def test_check_alerts_skips_assets_without_price_data(isolated_db: Path) -> None:
    aid = _seed_asset()
    svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    # No PricePoints for this asset → skip, don't error.
    assert svc.check_alerts() == 0


def test_check_alerts_uses_latest_bar(isolated_db: Path) -> None:
    aid = _seed_asset()
    now = datetime.now(UTC)
    # Create while under threshold, then a newer bar crosses over — should fire.
    _add_price(aid, Decimal("100.00"), ts=now - timedelta(hours=1))
    svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    _add_price(aid, Decimal("160.00"), ts=now)
    assert svc.check_alerts() == 1


def test_check_alerts_fires_each_independently(isolated_db: Path) -> None:
    aid1 = _seed_asset("AAPL", "Apple")
    aid2 = _seed_asset("MSFT", "Microsoft")
    # Create all alerts before any price exists so none is rejected as already
    # crossed; the third (above 300) genuinely won't fire at price 200.
    svc.create_alert(asset_id=aid1, threshold=Decimal("150.00"), direction="above")
    svc.create_alert(asset_id=aid2, threshold=Decimal("100.00"), direction="below")
    svc.create_alert(asset_id=aid1, threshold=Decimal("300.00"), direction="above")
    _add_price(aid1, Decimal("200.00"))
    _add_price(aid2, Decimal("50.00"))
    assert svc.check_alerts() == 2


# ---------------------------------------------------------------------------
# pending notifications handshake
# ---------------------------------------------------------------------------


def test_list_pending_notifications_returns_only_fired_not_notified(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    a_fire = svc.create_alert(
        asset_id=aid, threshold=Decimal("150.00"), direction="above"
    )
    svc.create_alert(
        asset_id=aid, threshold=Decimal("250.00"), direction="above"
    )  # won't fire
    _add_price(aid, Decimal("200.00"))
    svc.check_alerts()
    pending = svc.list_pending_notifications()
    assert [p.id for p in pending] == [a_fire.id]

    # Once notified, it drops off the pending list.
    svc.mark_notified(a_fire.id)
    assert svc.list_pending_notifications() == []


def test_mark_notified_requires_triggered(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=1, direction="above")
    with pytest.raises(svc.AlertError):
        svc.mark_notified(a.id)


def test_mark_notified_is_idempotent(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(asset_id=aid, threshold=Decimal("150.00"), direction="above")
    _add_price(aid, Decimal("200.00"))
    svc.check_alerts()
    first = svc.mark_notified(a.id)
    second = svc.mark_notified(a.id)
    assert first.notified_at is not None
    assert second.notified_at is not None
    # SQLite strips tzinfo on read, so the first call's value is tz-aware
    # (just-assigned in-memory) while the second is naive (reloaded). Compare
    # naively — we only care that the stamp didn't move.
    assert second.notified_at.replace(tzinfo=None) == first.notified_at.replace(
        tzinfo=None
    )


# ---------------------------------------------------------------------------
# Sentiment alerts (Phase K)
# ---------------------------------------------------------------------------


def test_create_sentiment_alert_happy(isolated_db: Path) -> None:
    aid = _seed_asset()
    a = svc.create_alert(
        asset_id=aid,
        threshold=Decimal("-0.3"),
        direction="below",
        metric="sentiment",
        window_days=7,
    )
    assert a.metric == AlertMetric.SENTIMENT
    assert a.window_days == 7
    assert a.threshold == Decimal("-0.3")


def test_create_sentiment_alert_requires_window_days(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError) as exc:
        svc.create_alert(
            asset_id=aid,
            threshold=Decimal("0.5"),
            direction="above",
            metric="sentiment",
        )
    assert "window_days" in str(exc.value).lower()


def test_create_price_alert_rejects_window_days(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError) as exc:
        svc.create_alert(
            asset_id=aid,
            threshold=Decimal("100"),
            direction="above",
            metric="price",
            window_days=7,
        )
    assert "window_days" in str(exc.value).lower()


def test_create_sentiment_alert_threshold_must_be_in_range(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError):
        svc.create_alert(
            asset_id=aid,
            threshold=Decimal("5"),  # outside [-1, +1]
            direction="above",
            metric="sentiment",
            window_days=7,
        )


def test_create_sentiment_alert_window_days_bounds(isolated_db: Path) -> None:
    aid = _seed_asset()
    # Below the minimum.
    with pytest.raises(svc.AlertError):
        svc.create_alert(
            asset_id=aid,
            threshold=Decimal("0.3"),
            direction="above",
            metric="sentiment",
            window_days=0,
        )
    # Above the maximum.
    with pytest.raises(svc.AlertError):
        svc.create_alert(
            asset_id=aid,
            threshold=Decimal("0.3"),
            direction="above",
            metric="sentiment",
            window_days=400,
        )


def test_unknown_metric_rejected(isolated_db: Path) -> None:
    aid = _seed_asset()
    with pytest.raises(svc.AlertError):
        svc.create_alert(
            asset_id=aid,
            threshold=Decimal("0.3"),
            direction="above",
            metric="volatility",  # not a known metric
        )


def test_check_alerts_sentiment_fires_below(isolated_db: Path) -> None:
    """7-day rolling mean of two articles at -0.5 each crosses a -0.3
    threshold from below; alert fires."""
    aid = _seed_asset()
    _add_article(aid, sentiment=-0.5, headline="bad")
    _add_article(aid, sentiment=-0.5, headline="bad", url="https://test/2")
    a = svc.create_alert(
        asset_id=aid,
        threshold=Decimal("-0.3"),
        direction="below",
        metric="sentiment",
        window_days=7,
    )
    fired = svc.check_alerts()
    assert fired == 1
    refreshed = svc.get_alert(a.id)
    assert refreshed.triggered_at is not None
    assert refreshed.current_value is not None
    assert refreshed.current_value <= Decimal("-0.3")


def test_check_alerts_sentiment_does_not_fire_when_above(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    _add_article(aid, sentiment=0.6, headline="good")
    _add_article(aid, sentiment=0.4, headline="good", url="https://test/2")
    svc.create_alert(
        asset_id=aid,
        threshold=Decimal("-0.3"),
        direction="below",
        metric="sentiment",
        window_days=7,
    )
    assert svc.check_alerts() == 0


def test_check_alerts_sentiment_skips_when_no_articles_in_window(
    isolated_db: Path,
) -> None:
    """Sentiment alert with zero scored articles in window → no signal,
    no firing (rather than misfire on a synthetic 0)."""
    aid = _seed_asset()
    # Article exists but is older than window_days=7.
    _add_article(aid, sentiment=-0.5, days_ago=30)
    svc.create_alert(
        asset_id=aid,
        threshold=Decimal("-0.3"),
        direction="below",
        metric="sentiment",
        window_days=7,
    )
    assert svc.check_alerts() == 0


def test_check_alerts_sentiment_excludes_unscored_articles(
    isolated_db: Path,
) -> None:
    """An unscored article shouldn't drag the rolling mean toward zero."""
    aid = _seed_asset()
    _add_article(aid, sentiment=-0.5)
    _add_article(aid, sentiment=None, url="https://test/unscored")  # excluded
    svc.create_alert(
        asset_id=aid,
        threshold=Decimal("-0.3"),
        direction="below",
        metric="sentiment",
        window_days=7,
    )
    # With only the -0.5 article counting, the mean is -0.5 → fires.
    assert svc.check_alerts() == 1


def test_check_alerts_mixed_metrics_in_one_pass(isolated_db: Path) -> None:
    """check_alerts handles price and sentiment alerts side-by-side."""
    aid = _seed_asset()
    _add_article(aid, sentiment=-0.5)
    _add_article(aid, sentiment=-0.5, url="https://test/2")
    # Create the price alert before the crossing price exists (else it'd be
    # rejected as already-crossed); the sentiment alert is exempt from that.
    svc.create_alert(
        asset_id=aid, threshold=Decimal("150"), direction="above"
    )  # price → fires
    svc.create_alert(
        asset_id=aid,
        threshold=Decimal("-0.3"),
        direction="below",
        metric="sentiment",
        window_days=7,
    )  # sentiment → fires
    _add_price(aid, Decimal("200"))
    assert svc.check_alerts() == 2


def test_list_alerts_hydrates_current_value_for_sentiment(
    isolated_db: Path,
) -> None:
    aid = _seed_asset()
    _add_article(aid, sentiment=0.5)
    _add_article(aid, sentiment=0.5, url="https://test/2")
    svc.create_alert(
        asset_id=aid,
        threshold=Decimal("0.4"),
        direction="above",
        metric="sentiment",
        window_days=7,
    )
    listed = svc.list_alerts()
    assert len(listed) == 1
    assert listed[0].metric == AlertMetric.SENTIMENT
    assert listed[0].current_value is not None
    # Mean of two 0.5 scores = 0.5
    assert abs(float(listed[0].current_value) - 0.5) < 1e-6


def test_mark_notified_unknown_alert(isolated_db: Path) -> None:
    with pytest.raises(svc.AlertNotFoundError):
        svc.mark_notified(9999)
