"""test_errors.py — errors.py の契約面テスト（Red フェーズ）。

対象:
- ErrorCode(Enum): 必須メンバーの存在確認（§4 + §13.1 DC-AM-002/DC-AS-003）
- ClipwrightError: code / message / hint を保持する例外クラス

このテストは errors.py が未実装のため ImportError で失敗する（Red）。
"""

from __future__ import annotations

import pytest

# --- Import（errors.py 未実装のため ImportError が発生する → Red） ---
from clipwright.errors import ClipwrightError, ErrorCode


# ===========================================================================
# ErrorCode — 必須メンバー存在確認
# ===========================================================================


class TestErrorCodeMembers:
    """ErrorCode Enum の必須メンバーが全て定義されていることを確認する。"""

    @pytest.mark.parametrize(
        "name",
        [
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "FILE_NOT_FOUND",
            "PATH_NOT_ALLOWED",
            "SUBPROCESS_FAILED",
            "SUBPROCESS_TIMEOUT",
            "PROBE_FAILED",
            "OTIO_ERROR",
            "PROJECT_NOT_FOUND",
            "PROJECT_EXISTS",
            "UNSUPPORTED_OPERATION",
            "INTERNAL",           # §13.1 DC-AM-002 で追加
            "TRACK_NOT_FOUND",    # §13.1 DC-AS-003 で追加
        ],
    )
    def test_member_exists(self, name: str) -> None:
        """必須メンバーが ErrorCode に存在する。"""
        assert hasattr(ErrorCode, name), f"ErrorCode.{name} が定義されていません"

    def test_is_str_enum(self) -> None:
        """ErrorCode が str のサブクラスである（str, Enum 継承）。"""
        assert issubclass(ErrorCode, str)

    def test_value_is_string(self) -> None:
        """値が文字列として取得できる。"""
        code = ErrorCode.DEPENDENCY_MISSING
        assert isinstance(code.value, str)

    @pytest.mark.parametrize(
        "name, expected_value",
        [
            # 値は name と一致することを期待（str Enum の慣習）
            ("DEPENDENCY_MISSING", "DEPENDENCY_MISSING"),
            ("INVALID_INPUT", "INVALID_INPUT"),
            ("FILE_NOT_FOUND", "FILE_NOT_FOUND"),
            ("PATH_NOT_ALLOWED", "PATH_NOT_ALLOWED"),
            ("SUBPROCESS_FAILED", "SUBPROCESS_FAILED"),
            ("SUBPROCESS_TIMEOUT", "SUBPROCESS_TIMEOUT"),
            ("PROBE_FAILED", "PROBE_FAILED"),
            ("OTIO_ERROR", "OTIO_ERROR"),
            ("PROJECT_NOT_FOUND", "PROJECT_NOT_FOUND"),
            ("PROJECT_EXISTS", "PROJECT_EXISTS"),
            ("UNSUPPORTED_OPERATION", "UNSUPPORTED_OPERATION"),
            ("INTERNAL", "INTERNAL"),
            ("TRACK_NOT_FOUND", "TRACK_NOT_FOUND"),
        ],
    )
    def test_value_matches_name(self, name: str, expected_value: str) -> None:
        """ErrorCode の値は名前文字列と一致する（JSON 境界でのシリアライズ互換）。"""
        member = ErrorCode[name]
        assert member.value == expected_value

    def test_can_construct_from_string(self) -> None:
        """文字列から ErrorCode を逆引きできる。"""
        code = ErrorCode("INVALID_INPUT")
        assert code == ErrorCode.INVALID_INPUT

    def test_all_required_members_count(self) -> None:
        """必須の13メンバーが全員いる（追加は構わないが欠損は禁止）。"""
        required = {
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "FILE_NOT_FOUND",
            "PATH_NOT_ALLOWED",
            "SUBPROCESS_FAILED",
            "SUBPROCESS_TIMEOUT",
            "PROBE_FAILED",
            "OTIO_ERROR",
            "PROJECT_NOT_FOUND",
            "PROJECT_EXISTS",
            "UNSUPPORTED_OPERATION",
            "INTERNAL",
            "TRACK_NOT_FOUND",
        }
        actual = {m.name for m in ErrorCode}
        missing = required - actual
        assert not missing, f"ErrorCode に以下のメンバーが欠落しています: {missing}"


# ===========================================================================
# ClipwrightError
# ===========================================================================


class TestClipwrightError:
    """ClipwrightError 例外クラスの基本契約。"""

    def test_is_exception(self) -> None:
        """ClipwrightError は Exception のサブクラス。"""
        assert issubclass(ClipwrightError, Exception)

    def test_construct_and_attributes(self) -> None:
        """code / message / hint を保持する。"""
        err = ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="ファイルが見つかりません",
            hint="パスを確認してください",
        )
        assert err.code == ErrorCode.FILE_NOT_FOUND
        assert err.message == "ファイルが見つかりません"
        assert err.hint == "パスを確認してください"

    def test_can_be_raised_and_caught(self) -> None:
        """raise して ClipwrightError で捕捉できる。"""
        with pytest.raises(ClipwrightError) as exc_info:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="不正な入力です",
                hint="入力値を確認してください",
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_also_catchable_as_exception(self) -> None:
        """Exception としても捕捉できる。"""
        with pytest.raises(Exception):
            raise ClipwrightError(
                code=ErrorCode.INTERNAL,
                message="予期しないエラー",
                hint="再現条件を添えて報告してください",
            )

    @pytest.mark.parametrize(
        "code",
        [
            ErrorCode.DEPENDENCY_MISSING,
            ErrorCode.SUBPROCESS_FAILED,
            ErrorCode.SUBPROCESS_TIMEOUT,
            ErrorCode.PROBE_FAILED,
            ErrorCode.OTIO_ERROR,
            ErrorCode.PROJECT_NOT_FOUND,
            ErrorCode.PROJECT_EXISTS,
            ErrorCode.PATH_NOT_ALLOWED,
            ErrorCode.UNSUPPORTED_OPERATION,
            ErrorCode.INTERNAL,
            ErrorCode.TRACK_NOT_FOUND,
        ],
    )
    def test_all_error_codes_usable(self, code: ErrorCode) -> None:
        """全エラーコードで ClipwrightError を構築できる。"""
        err = ClipwrightError(code=code, message="テスト", hint="ヒント")
        assert err.code == code

    def test_code_is_error_code_type(self) -> None:
        """code 属性は ErrorCode 型。"""
        err = ClipwrightError(
            code=ErrorCode.OTIO_ERROR,
            message="OTIO エラー",
            hint="OTIO ファイルを確認してください",
        )
        assert isinstance(err.code, ErrorCode)

    def test_dependency_missing_message_hint_pattern(self) -> None:
        """DEPENDENCY_MISSING は Windows 向け hint を含むことを想定する（契約確認）。"""
        err = ClipwrightError(
            code=ErrorCode.DEPENDENCY_MISSING,
            message="ffprobe が見つかりません",
            hint="winget install Gyan.FFmpeg で導入し、シェルを再起動するか CLIPWRIGHT_FFPROBE に実行ファイルのフルパスを設定してください",
        )
        # hint が空でないこと（アクション可能であることが必須・§4 規約）
        assert len(err.hint) > 0
        assert err.message == "ffprobe が見つかりません"
