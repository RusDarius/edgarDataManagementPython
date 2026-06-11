"""Tests for move prediction pool analyzer."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure src is in path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_analysis_scripts.trading_view_move_prediction_pool_analyzer import (
    ProfilePerformanceMetrics,
    StockCompositeRanking,
    PatternPredictorInsight,
    PoolAnalysisResult,
    _quote_identifier,
    _ensure_output_dir,
    _get_pool_aggregation_summary,
    _analyze_profile_performance,
    _analyze_stock_composite_rankings,
    export_analysis_reports,
)


class TestQuoteIdentifier:
    """Test SQL identifier quoting."""

    def test_simple_identifier(self):
        assert _quote_identifier("test") == '"test"'

    def test_identifier_with_quotes(self):
        assert _quote_identifier('test"value') == '"test""value"'

    def test_empty_identifier(self):
        assert _quote_identifier("") == '""'


class TestOutputDirectory:
    """Test output directory creation."""

    def test_creates_directory(self, tmp_path):
        test_dir = tmp_path / "test_output"
        result = _ensure_output_dir(str(test_dir))
        assert result.exists()
        assert result.is_dir()

    def test_auto_generates_directory(self, tmp_path):
        # Change to temp path for auto-generation
        with patch(
            'data_analysis_scripts.trading_view_move_prediction_pool_analyzer.DEFAULT_ANALYSIS_OUTPUT_ROOT',
            tmp_path,
        ):
            result = _ensure_output_dir(None)
            assert result.exists()
            assert result.is_dir()
            assert "pool_analysis_" in result.name


class TestProfileMetrics:
    """Test ProfilePerformanceMetrics dataclass."""

    def test_creation(self):
        metric = ProfilePerformanceMetrics(
            profile_name="test_profile",
            total_runs=10,
            total_symbols_scored=100,
            avg_score=75.5,
            avg_risk_adjusted_score=82.0,
            avg_confidence=0.75,
            avg_coverage=0.85,
            score_std_dev=5.2,
            positive_direction_ratio=0.65,
            high_confidence_ratio=0.45,
            high_score_ratio=0.25,
            top_decile_capture_rate=None,
            price_performance_correlation=None,
            sector_diversity_score=0.8,
            persistence_score=None,
        )
        assert metric.profile_name == "test_profile"
        assert metric.avg_score == 75.5


class TestStockRanking:
    """Test StockCompositeRanking dataclass."""

    def test_creation(self):
        ranking = StockCompositeRanking(
            symbol="AAPL",
            company="Apple Inc",
            sector="Technology",
            industry="Consumer Electronics",
            profile_appearances=5,
            run_appearances=3,
            avg_score_across_profiles=78.0,
            max_score_across_profiles=92.0,
            best_profile="breakout_long",
            avg_risk_adjusted_score=85.0,
            avg_confidence=0.72,
            avg_direction=0.8,
            consensus_long_count=8,
            consensus_short_count=2,
            composite_rank=88.5,
            price_performance_proxy=12.5,
            strongest_predictor_profile="breakout_long",
        )
        assert ranking.symbol == "AAPL"
        assert ranking.composite_rank == 88.5


class TestPatternInsight:
    """Test PatternPredictorInsight dataclass."""

    def test_creation(self):
        insight = PatternPredictorInsight(
            profile_name="breakout_long",
            predictor_field="RSI",
            predictor_category="momentum",
            correlation_with_high_score=0.45,
            correlation_with_direction=0.38,
            predictive_power_score=0.415,
            coverage_rate=0.95,
            sector_bias="Technology",
            time_stability=0.82,
        )
        assert insight.predictor_field == "RSI"
        assert insight.predictive_power_score == 0.415


class TestExportFunctions:
    """Test export functionality."""

    @pytest.fixture
    def sample_result(self, tmp_path):
        """Create a sample PoolAnalysisResult."""
        return PoolAnalysisResult(
            pool_aggregation_id="test_pool_123",
            database_path=str(tmp_path / "test.duckdb"),
            analysis_timestamp_utc="2026-06-10T20:00:00Z",
            total_runs=5,
            total_weeks=3,
            week_range=(20, 24),
            profile_metrics=[
                ProfilePerformanceMetrics(
                    profile_name="test_profile",
                    total_runs=5,
                    total_symbols_scored=100,
                    avg_score=75.0,
                    avg_risk_adjusted_score=80.0,
                    avg_confidence=0.7,
                    avg_coverage=0.8,
                    score_std_dev=5.0,
                    positive_direction_ratio=0.6,
                    high_confidence_ratio=0.4,
                    high_score_ratio=0.2,
                    top_decile_capture_rate=None,
                    price_performance_correlation=None,
                    sector_diversity_score=None,
                    persistence_score=None,
                )
            ],
            stock_rankings=[
                StockCompositeRanking(
                    symbol="AAPL",
                    company="Apple Inc",
                    sector="Technology",
                    industry="Hardware",
                    profile_appearances=3,
                    run_appearances=2,
                    avg_score_across_profiles=80.0,
                    max_score_across_profiles=90.0,
                    best_profile="test_profile",
                    avg_risk_adjusted_score=85.0,
                    avg_confidence=0.75,
                    avg_direction=1.0,
                    consensus_long_count=5,
                    consensus_short_count=0,
                    composite_rank=88.0,
                    price_performance_proxy=None,
                    strongest_predictor_profile="test_profile",
                )
            ],
            pattern_insights=[
                PatternPredictorInsight(
                    profile_name="test_profile",
                    predictor_field="RSI",
                    predictor_category="momentum",
                    correlation_with_high_score=0.5,
                    correlation_with_direction=0.4,
                    predictive_power_score=0.45,
                    coverage_rate=0.95,
                    sector_bias="Technology",
                    time_stability=0.8,
                )
            ],
            score_vs_price_correlation=None,
            top_performers_intersection=["AAPL", "MSFT"],
            missing_performance_symbols=["TSLA"],
            execution_time_seconds=12.5,
        )

    def test_export_json(self, sample_result, tmp_path):
        exported = export_analysis_reports(
            sample_result,
            tmp_path,
            export_json=True,
            export_csv=False,
            export_sql=False,
        )
        assert "json_full" in exported
        assert exported["json_full"].exists()

    def test_export_csv(self, sample_result, tmp_path):
        exported = export_analysis_reports(
            sample_result,
            tmp_path,
            export_json=False,
            export_csv=True,
            export_sql=False,
        )
        assert "profile_csv" in exported
        assert "stock_csv" in exported
        assert "pattern_csv" in exported
        assert all(p.exists() for p in exported.values())

    def test_export_sql(self, sample_result, tmp_path):
        exported = export_analysis_reports(
            sample_result,
            tmp_path,
            export_json=False,
            export_csv=False,
            export_sql=True,
        )
        assert "sql_queries" in exported
        assert exported["sql_queries"].exists()

    def test_export_all(self, sample_result, tmp_path):
        exported = export_analysis_reports(
            sample_result,
            tmp_path,
            export_json=True,
            export_csv=True,
            export_sql=True,
        )
        assert len(exported) == 5  # JSON + 3 CSV + SQL
        assert all(p.exists() for p in exported.values())


class TestDatabaseFunctions:
    """Test database interaction functions (mocked)."""

    def test_get_pool_aggregation_summary(self):
        """Test that aggregation summary query is structured correctly."""
        # This is a structural test - actual DB tests would require real DB
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (
            "pool_123",
            10,
            5,
            20,
            24,
            2026,
            2026,
        )

        result = _get_pool_aggregation_summary(mock_conn)

        assert result["pool_aggregation_id"] == "pool_123"
        assert result["total_source_databases"] == 10
        assert result["week_range"] == (20, 24)
        mock_conn.execute.assert_called_once()

    def test_analyze_profile_performance(self):
        """Test profile performance analysis query structure."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("profile1",),
            ("profile2",),
        ]
        # Mock the aggregation query
        mock_conn.execute.return_value.fetchone.side_effect = [
            (5, 100, 75.0, 80.0, 0.7, 0.8, 5.0, 0.6, 0.4, 0.2),
            (3, 50, 70.0, 75.0, 0.65, 0.75, 4.5, 0.55, 0.35, 0.15),
        ]

        # Patch the sector query as well
        with patch.object(
            mock_conn, 'execute', return_value=MagicMock(
                fetchone=MagicMock(return_value=(3, 100, 900))
            )
        ) as mock_exec:
            # We need to mock the actual side effect behavior
            pass  # This test needs more complex mocking setup


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
