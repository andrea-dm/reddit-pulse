"""Contract tests for the :mod:`reddit.core.errors` exception hierarchy.

The hierarchy is a public contract: the CLI catches ``ConfigError`` to emit a
``parser.error`` message and ``RedditError`` to exit with status 1, and
existing call sites still catch ``KeyError`` for an unknown family.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from reddit.core.errors import (
    ConfigError,
    CorpusUnavailableError,
    RedditError,
    UndeclaredLabelError,
    UnknownFamilyError,
    UnsupportedMethodError,
)

CONFIG_ERRORS = [UnknownFamilyError, UnsupportedMethodError, UndeclaredLabelError]
ALL_ERRORS = [ConfigError, CorpusUnavailableError, *CONFIG_ERRORS]


class TestErrorsModule:
    """The domain exception hierarchy."""

    @pytest.mark.unit
    class TestUnits:
        @pytest.mark.parametrize("error_type", ALL_ERRORS)
        def test_every_domain_error_is_a_reddit_error(self, error_type: type[Exception]) -> None:
            assert issubclass(error_type, RedditError)

        def test_reddit_error_is_a_plain_exception_not_a_base_exception_escape_hatch(self) -> None:
            assert issubclass(RedditError, Exception)

        @pytest.mark.parametrize("error_type", CONFIG_ERRORS)
        def test_user_fixable_problems_are_config_errors(self, error_type: type[Exception]) -> None:
            """The CLI turns these into ``parser.error`` messages rather than tracebacks."""
            assert issubclass(error_type, ConfigError)

        def test_a_missing_corpus_is_not_a_configuration_problem(self) -> None:
            assert issubclass(CorpusUnavailableError, RedditError)
            assert not issubclass(CorpusUnavailableError, ConfigError)

        def test_unknown_family_is_both_a_config_error_and_a_key_error(self) -> None:
            assert issubclass(UnknownFamilyError, ConfigError)
            assert issubclass(UnknownFamilyError, KeyError)

        def test_unknown_family_can_be_caught_as_a_key_error(self) -> None:
            with pytest.raises(KeyError):
                raise UnknownFamilyError("Unknown model family `nope`.")

        def test_unknown_family_can_be_caught_as_a_config_error(self) -> None:
            with pytest.raises(ConfigError):
                raise UnknownFamilyError("Unknown model family `nope`.")

        def test_unknown_family_renders_without_the_key_error_quoting(self) -> None:
            """``KeyError.__str__`` would wrap the message in quotes; this one must not."""
            message = "Unknown model family `nope`. Available: bert, gemma"

            assert str(UnknownFamilyError(message)) == message
            assert str(KeyError(message)) != message

        def test_unknown_family_without_arguments_renders_as_the_empty_string(self) -> None:
            assert str(UnknownFamilyError()) == ""

        @pytest.mark.parametrize("error_type", ALL_ERRORS)
        def test_errors_preserve_their_message_as_the_first_argument(self, error_type: type[Exception]) -> None:
            error = error_type("boom")

            assert error.args == ("boom",)

        def test_errors_can_be_chained_from_a_cause(self) -> None:
            cause = ValueError("root cause")

            try:
                try:
                    raise cause
                except ValueError as e:
                    raise CorpusUnavailableError("answers file missing") from e
            except CorpusUnavailableError as raised:
                assert raised.__cause__ is cause

        def test_a_bare_reddit_error_does_not_swallow_unrelated_exceptions(self) -> None:
            unrelated = ValueError("unrelated")

            with pytest.raises(ValueError):
                try:
                    raise unrelated
                except RedditError:  # pragma: no cover - must not match
                    pytest.fail("ValueError must not be caught as a RedditError")

    @pytest.mark.contracts
    class TestContracts:
        @given(message=st.text(max_size=60))
        def test_unknown_family_str_is_the_identity_on_its_message(self, message: str) -> None:
            assert str(UnknownFamilyError(message)) == message

        @given(message=st.text(max_size=60))
        def test_every_domain_error_is_catchable_as_reddit_error(self, message: str) -> None:
            for error_type in ALL_ERRORS:
                with pytest.raises(RedditError):
                    raise error_type(message)
