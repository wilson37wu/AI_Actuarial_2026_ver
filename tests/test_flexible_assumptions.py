"""
Unit tests for FlexibleAssumptionProvider.

Tests cover:
- Multi-dimensional lookup
- Interpolation (linear and step)
- Extrapolation
- Caching
- Error handling
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import json
import tempfile

import pandas as pd
import pytest
from par_model_v2.assumptions.flexible_provider import FlexibleAssumptionProvider, TableMetadata


@pytest.fixture
def temp_assumption_dir():
    """Create temporary directory with test assumption files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create metadata.json
        metadata = {
            "mortality_qx": {
                "file": "mortality_qx.csv",
                "dimensions": ["product", "gender", "age", "policy_year"],
                "value_column": "qx",
                "interpolation": "linear",
                "extrapolation": "constant",
            },
            "lapse": {
                "file": "lapse.csv",
                "dimensions": ["product", "policy_year", "age_band"],
                "value_column": "lapse_rate",
                "interpolation": "step",
                "extrapolation": "constant",
            },
        }

        with open(tmpdir / "metadata.json", "w") as f:
            json.dump(metadata, f)

        # Create mortality table
        mortality_data = []
        for product in ["WL", "Pension"]:
            for gender in ["M", "F"]:
                for age in [25, 30, 35, 40, 45, 50]:
                    for py in [1, 2, 3]:
                        qx = 0.0005 * (age / 25) * (1.5 if gender == "M" else 1.0)
                        mortality_data.append(
                            {
                                "product": product,
                                "gender": gender,
                                "age": age,
                                "policy_year": py,
                                "qx": qx,
                            }
                        )

        pd.DataFrame(mortality_data).to_csv(tmpdir / "mortality_qx.csv", index=False)

        # Create lapse table
        lapse_data = []
        for product in ["WL", "Pension"]:
            for py in [1, 2, 3, 5]:
                for age_band in ["20-30", "30-40", "40-50"]:
                    lapse_rate = 0.15 / py * (0.8 if product == "Pension" else 1.0)
                    lapse_data.append(
                        {
                            "product": product,
                            "policy_year": py,
                            "age_band": age_band,
                            "lapse_rate": lapse_rate,
                        }
                    )

        pd.DataFrame(lapse_data).to_csv(tmpdir / "lapse.csv", index=False)

        yield tmpdir


class TestFlexibleAssumptionProvider:
    """Test suite for FlexibleAssumptionProvider."""

    def test_initialization(self, temp_assumption_dir):
        """Test provider initialization and table loading."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir), "metadata.json")

        assert len(provider.tables) == 2
        assert "mortality_qx" in provider.tables
        assert "lapse" in provider.tables
        assert len(provider.metadata) == 2

    def test_exact_match_lookup(self, temp_assumption_dir):
        """Test exact match lookup without interpolation."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Test mortality lookup
        qx = provider.get_mortality("WL", "M", 30, policy_year=1)
        assert qx > 0
        assert isinstance(qx, float)

        # Test lapse lookup
        lapse = provider.get_lapse("Pension", 2, age="30-40")
        assert lapse > 0
        assert isinstance(lapse, float)

    def test_linear_interpolation(self, temp_assumption_dir):
        """Test linear interpolation for numeric dimensions."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Get values at known points
        qx_30 = provider.get_mortality("WL", "M", 30, policy_year=1)
        qx_35 = provider.get_mortality("WL", "M", 35, policy_year=1)

        # Interpolate at midpoint
        qx_32_5 = provider.get_value(
            "mortality_qx", product="WL", gender="M", age=32.5, policy_year=1
        )

        # Should be between the two values
        assert qx_30 < qx_32_5 < qx_35

        # Should be approximately at midpoint (linear interpolation)
        expected = (qx_30 + qx_35) / 2
        assert abs(qx_32_5 - expected) < 0.0001

    def test_extrapolation_constant(self, temp_assumption_dir):
        """Test constant extrapolation beyond data range."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Get value at boundary
        qx_50 = provider.get_mortality("WL", "M", 50, policy_year=1)

        # Extrapolate beyond boundary
        qx_60 = provider.get_value("mortality_qx", product="WL", gender="M", age=60, policy_year=1)

        # Should use constant extrapolation (same as boundary)
        assert qx_60 == qx_50

    def test_caching(self, temp_assumption_dir):
        """Test that caching improves performance."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # First lookup (not cached)
        qx1 = provider.get_mortality("WL", "M", 30, policy_year=1)

        # Second lookup (should be cached)
        qx2 = provider.get_mortality("WL", "M", 30, policy_year=1)

        assert qx1 == qx2
        assert len(provider.cache) > 0

        # Clear cache
        provider.clear_cache()
        assert len(provider.cache) == 0

    def test_multi_dimensional_lookup(self, temp_assumption_dir):
        """Test lookup with multiple dimensions."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Test with all dimensions specified
        qx = provider.get_value("mortality_qx", product="WL", gender="M", age=35, policy_year=2)

        assert qx > 0

        # Test with different product
        qx_pension = provider.get_value(
            "mortality_qx", product="Pension", gender="M", age=35, policy_year=2
        )

        # Should be same (no product differentiation in test data)
        assert qx == qx_pension

    def test_step_interpolation(self, temp_assumption_dir):
        """Test step interpolation (nearest neighbor)."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Lapse table uses step interpolation
        # Should return nearest value for policy_year
        lapse_1 = provider.get_lapse("WL", 1, age="30-40")
        lapse_2 = provider.get_lapse("WL", 2, age="30-40")

        # Interpolate at 1.4 (closer to 1)
        lapse_1_4 = provider.get_value("lapse", product="WL", policy_year=1.4, age_band="30-40")

        # Should be closer to lapse_1 (step interpolation)
        assert abs(lapse_1_4 - lapse_1) < abs(lapse_1_4 - lapse_2)

    def test_missing_dimension(self, temp_assumption_dir):
        """Test behavior when dimension is missing."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Lookup with missing dimension should still work (partial match)
        qx = provider.get_value(
            "mortality_qx",
            product="WL",
            gender="M",
            age=30,
            # policy_year missing
        )

        assert qx > 0

    def test_invalid_table_name(self, temp_assumption_dir):
        """Test error handling for invalid table name."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        with pytest.raises(ValueError, match="not loaded"):
            provider.get_value("nonexistent_table", product="WL")

    def test_get_table_info(self, temp_assumption_dir):
        """Test table information retrieval."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        info = provider.get_table_info("mortality_qx")

        assert info["name"] == "mortality_qx"
        assert "dimensions" in info
        assert "value_column" in info
        assert info["rows"] > 0
        assert "value_range" in info

    def test_list_tables(self, temp_assumption_dir):
        """Test listing all loaded tables."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        tables = provider.list_tables()

        assert len(tables) == 2
        assert "mortality_qx" in tables
        assert "lapse" in tables

    def test_convenience_methods(self, temp_assumption_dir):
        """Test convenience methods for common assumptions."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        # Test get_mortality
        qx = provider.get_mortality("WL", "M", 35, "N", 1)
        assert qx > 0

        # Test get_lapse
        lapse = provider.get_lapse("Pension", 2, age="30-40")
        assert lapse > 0

    def test_gender_differentiation(self, temp_assumption_dir):
        """Test that gender affects mortality rates."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        qx_male = provider.get_mortality("WL", "M", 35, policy_year=1)
        qx_female = provider.get_mortality("WL", "F", 35, policy_year=1)

        # Males should have higher mortality
        assert qx_male > qx_female

    def test_age_progression(self, temp_assumption_dir):
        """Test that mortality increases with age."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        qx_25 = provider.get_mortality("WL", "M", 25, policy_year=1)
        qx_35 = provider.get_mortality("WL", "M", 35, policy_year=1)
        qx_45 = provider.get_mortality("WL", "M", 45, policy_year=1)

        # Mortality should increase with age
        assert qx_25 < qx_35 < qx_45

    def test_policy_year_effect_on_lapse(self, temp_assumption_dir):
        """Test that lapse rates decrease with policy year."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        lapse_1 = provider.get_lapse("WL", 1, age="30-40")
        lapse_2 = provider.get_lapse("WL", 2, age="30-40")
        lapse_5 = provider.get_lapse("WL", 5, age="30-40")

        # Lapse should decrease with policy year
        assert lapse_1 > lapse_2 > lapse_5

    def test_product_differentiation_lapse(self, temp_assumption_dir):
        """Test that lapse rates differ by product."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        lapse_wl = provider.get_lapse("WL", 1, age="30-40")
        lapse_pension = provider.get_lapse("Pension", 1, age="30-40")

        # Pension should have lower lapse
        assert lapse_pension < lapse_wl

    def test_metadata_validation(self, temp_assumption_dir):
        """Test that metadata is properly validated."""
        provider = FlexibleAssumptionProvider(str(temp_assumption_dir))

        meta = provider.metadata["mortality_qx"]

        assert meta.name == "mortality_qx"
        assert "age" in meta.dimensions
        assert meta.value_column == "qx"
        assert meta.interpolation == "linear"
        assert meta.extrapolation == "constant"


class TestTableMetadata:
    """Test TableMetadata dataclass."""

    def test_metadata_creation(self):
        """Test creating TableMetadata."""
        meta = TableMetadata(
            name="test_table",
            file="test.csv",
            dimensions=["age", "gender"],
            value_column="value",
            interpolation="linear",
            extrapolation="constant",
            description="Test table",
        )

        assert meta.name == "test_table"
        assert len(meta.dimensions) == 2
        assert meta.interpolation == "linear"

    def test_metadata_defaults(self):
        """Test default values for TableMetadata."""
        meta = TableMetadata(
            name="test_table", file="test.csv", dimensions=["age"], value_column="value"
        )

        assert meta.interpolation == "linear"
        assert meta.extrapolation == "constant"
        assert meta.description is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
