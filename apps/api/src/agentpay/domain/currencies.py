from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from agentpay.domain.errors import InvalidCurrencyError


@dataclass(frozen=True, slots=True)
class CurrencyInfo:
    """ISO 4217 currency metadata."""

    code: str
    exponent: int
    numeric_code: str
    name: str


_CURRENCIES: Final[tuple[CurrencyInfo, ...]] = (
    # Exponent 0: zero-decimal currencies
    CurrencyInfo(code="JPY", exponent=0, numeric_code="392", name="Japanese Yen"),
    CurrencyInfo(code="KRW", exponent=0, numeric_code="410", name="South Korean Won"),
    CurrencyInfo(code="VND", exponent=0, numeric_code="704", name="Vietnamese Dong"),
    CurrencyInfo(code="CLP", exponent=0, numeric_code="152", name="Chilean Peso"),
    CurrencyInfo(code="PYG", exponent=0, numeric_code="600", name="Paraguayan Guarani"),
    CurrencyInfo(code="RWF", exponent=0, numeric_code="646", name="Rwandan Franc"),
    CurrencyInfo(code="UGX", exponent=0, numeric_code="800", name="Ugandan Shilling"),
    CurrencyInfo(code="BIF", exponent=0, numeric_code="108", name="Burundian Franc"),
    CurrencyInfo(code="DJF", exponent=0, numeric_code="262", name="Djiboutian Franc"),
    CurrencyInfo(code="GNF", exponent=0, numeric_code="324", name="Guinean Franc"),
    CurrencyInfo(code="KMF", exponent=0, numeric_code="174", name="Comorian Franc"),
    CurrencyInfo(code="VUV", exponent=0, numeric_code="548", name="Vanuatu Vatu"),
    CurrencyInfo(code="XAF", exponent=0, numeric_code="950", name="Central African CFA Franc"),
    CurrencyInfo(code="XOF", exponent=0, numeric_code="952", name="West African CFA Franc"),
    CurrencyInfo(code="XPF", exponent=0, numeric_code="953", name="CFP Franc"),
    # Exponent 2: standard two-decimal currencies
    CurrencyInfo(code="USD", exponent=2, numeric_code="840", name="United States Dollar"),
    CurrencyInfo(code="EUR", exponent=2, numeric_code="978", name="Euro"),
    CurrencyInfo(code="GBP", exponent=2, numeric_code="826", name="Pound Sterling"),
    CurrencyInfo(code="INR", exponent=2, numeric_code="356", name="Indian Rupee"),
    CurrencyInfo(code="CAD", exponent=2, numeric_code="124", name="Canadian Dollar"),
    CurrencyInfo(code="AUD", exponent=2, numeric_code="036", name="Australian Dollar"),
    CurrencyInfo(code="CHF", exponent=2, numeric_code="756", name="Swiss Franc"),
    CurrencyInfo(code="CNY", exponent=2, numeric_code="156", name="Chinese Yuan"),
    CurrencyInfo(code="SGD", exponent=2, numeric_code="702", name="Singapore Dollar"),
    CurrencyInfo(code="HKD", exponent=2, numeric_code="344", name="Hong Kong Dollar"),
    CurrencyInfo(code="NZD", exponent=2, numeric_code="554", name="New Zealand Dollar"),
    CurrencyInfo(code="SEK", exponent=2, numeric_code="752", name="Swedish Krona"),
    CurrencyInfo(code="NOK", exponent=2, numeric_code="578", name="Norwegian Krone"),
    CurrencyInfo(code="DKK", exponent=2, numeric_code="208", name="Danish Krone"),
    CurrencyInfo(code="MXN", exponent=2, numeric_code="484", name="Mexican Peso"),
    CurrencyInfo(code="BRL", exponent=2, numeric_code="986", name="Brazilian Real"),
    CurrencyInfo(code="ZAR", exponent=2, numeric_code="710", name="South African Rand"),
    CurrencyInfo(code="AED", exponent=2, numeric_code="784", name="United Arab Emirates Dirham"),
    CurrencyInfo(code="SAR", exponent=2, numeric_code="682", name="Saudi Riyal"),
    CurrencyInfo(code="PLN", exponent=2, numeric_code="985", name="Polish Zloty"),
    CurrencyInfo(code="THB", exponent=2, numeric_code="764", name="Thai Baht"),
    CurrencyInfo(code="IDR", exponent=2, numeric_code="360", name="Indonesian Rupiah"),
    CurrencyInfo(code="MYR", exponent=2, numeric_code="458", name="Malaysian Ringgit"),
    CurrencyInfo(code="PHP", exponent=2, numeric_code="608", name="Philippine Peso"),
    CurrencyInfo(code="TRY", exponent=2, numeric_code="949", name="Turkish Lira"),
    CurrencyInfo(code="ILS", exponent=2, numeric_code="376", name="Israeli New Shekel"),
    CurrencyInfo(code="CZK", exponent=2, numeric_code="203", name="Czech Koruna"),
    CurrencyInfo(code="HUF", exponent=2, numeric_code="348", name="Hungarian Forint"),
    CurrencyInfo(code="RON", exponent=2, numeric_code="946", name="Romanian Leu"),
    CurrencyInfo(code="BGN", exponent=2, numeric_code="975", name="Bulgarian Lev"),
    CurrencyInfo(code="EGP", exponent=2, numeric_code="818", name="Egyptian Pound"),
    CurrencyInfo(code="KES", exponent=2, numeric_code="404", name="Kenyan Shilling"),
    CurrencyInfo(code="NGN", exponent=2, numeric_code="566", name="Nigerian Naira"),
    CurrencyInfo(code="XTS", exponent=2, numeric_code="963", name="Testing Currency Code"),
    # Exponent 3: three-decimal currencies
    CurrencyInfo(code="KWD", exponent=3, numeric_code="414", name="Kuwaiti Dinar"),
    CurrencyInfo(code="BHD", exponent=3, numeric_code="048", name="Bahraini Dinar"),
    CurrencyInfo(code="OMR", exponent=3, numeric_code="512", name="Omani Rial"),
    CurrencyInfo(code="JOD", exponent=3, numeric_code="400", name="Jordanian Dinar"),
    CurrencyInfo(code="TND", exponent=3, numeric_code="788", name="Tunisian Dinar"),
    CurrencyInfo(code="LYD", exponent=3, numeric_code="434", name="Libyan Dinar"),
    CurrencyInfo(code="IQD", exponent=3, numeric_code="368", name="Iraqi Dinar"),
    # Exponent 4: four-decimal currencies
    CurrencyInfo(code="CLF", exponent=4, numeric_code="990", name="Unidad de Fomento"),
    CurrencyInfo(code="UYW", exponent=4, numeric_code="927", name="Unidad Previsional"),
)

_REGISTRY: Final[dict[str, CurrencyInfo]] = {info.code: info for info in _CURRENCIES}


class CurrencyRegistry:
    """Registry of ISO 4217 currencies and their minor-unit exponents."""

    @classmethod
    def get(cls, code: str) -> CurrencyInfo:
        """Resolve CurrencyInfo for a given currency code.

        Fails closed with InvalidCurrencyError if the currency is not in the registry.
        """
        if not isinstance(code, str):
            raise InvalidCurrencyError(f"currency must be str, got {type(code).__name__}")
        normalized = code.strip().upper()
        if normalized not in _REGISTRY:
            raise InvalidCurrencyError(f"unsupported currency code: {code!r}")
        return _REGISTRY[normalized]

    @classmethod
    def get_exponent(cls, code: str) -> int:
        """Get the minor-unit exponent for a given currency code."""
        return cls.get(code).exponent

    @classmethod
    def is_supported(cls, code: object) -> bool:
        """Check whether a currency code is supported by the registry."""
        if not isinstance(code, str):
            return False
        return code.strip().upper() in _REGISTRY

    @classmethod
    def supported_currencies(cls) -> tuple[str, ...]:
        """Return a sorted tuple of all supported currency codes."""
        return tuple(sorted(_REGISTRY.keys()))


def get(code: str) -> CurrencyInfo:
    """Helper function to resolve CurrencyInfo from CurrencyRegistry."""
    return CurrencyRegistry.get(code)


def get_exponent(code: str) -> int:
    """Helper function to get currency exponent from CurrencyRegistry."""
    return CurrencyRegistry.get_exponent(code)


def is_supported(code: object) -> bool:
    """Helper function to check if currency is supported in CurrencyRegistry."""
    return CurrencyRegistry.is_supported(code)


def supported_currencies() -> tuple[str, ...]:
    """Helper function to list all supported currency codes."""
    return CurrencyRegistry.supported_currencies()
