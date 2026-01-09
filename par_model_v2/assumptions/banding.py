"""
Banding Helper Functions

Classify sum assured and premium amounts into standard bands
for assumption table lookup.
"""


def get_sa_band(sum_assured: float) -> str:
    """
    Classify sum assured into standard bands.

    Parameters
    ----------
    sum_assured : float
        Sum assured amount

    Returns
    -------
    str
        Band label (e.g., 'SA_0_100K', 'SA_100K_300K', etc.)
    """
    if sum_assured < 100_000:
        return "SA_0_100K"
    elif sum_assured < 300_000:
        return "SA_100K_300K"
    elif sum_assured < 1_000_000:
        return "SA_300K_1M"
    else:
        return "SA_1M_PLUS"


def get_premium_band(annual_premium: float) -> str:
    """
    Classify annual premium into standard bands.

    Parameters
    ----------
    annual_premium : float
        Annual premium amount

    Returns
    -------
    str
        Band label (e.g., 'PREM_0_10K', 'PREM_10K_30K', etc.)
    """
    if annual_premium < 10_000:
        return "PREM_0_10K"
    elif annual_premium < 30_000:
        return "PREM_10K_30K"
    elif annual_premium < 100_000:
        return "PREM_30K_100K"
    else:
        return "PREM_100K_PLUS"


def derive_annual_premium(
    sum_assured: float, premium_term: int, product_code: str, default_rate: float = 0.05
) -> float:
    """
    Derive annual premium if not provided.

    Simple heuristic: premium ≈ sum_assured * rate / premium_term

    Parameters
    ----------
    sum_assured : float
        Sum assured
    premium_term : int
        Premium payment term in years
    product_code : str
        Product code (WL, PEN, etc.)
    default_rate : float
        Default pricing rate

    Returns
    -------
    float
        Estimated annual premium
    """
    if premium_term <= 0:
        return 0.0

    # Simple approximation
    if product_code == "WL":
        rate = 0.04
    elif product_code == "PEN":
        rate = 0.06
    else:
        rate = default_rate

    return (sum_assured * rate) / premium_term
