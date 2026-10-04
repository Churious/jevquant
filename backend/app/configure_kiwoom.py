"""Interactive host-side secret input. Keys never become CLI arguments or output."""

import getpass
import os
from pathlib import Path
import sys
import tempfile


def main(provider="kiwoom"):
    if not sys.stdin.isatty():
        raise SystemExit(
            "SSH 터미널에서 직접 실행하세요. 비대화형 키 입력은 지원하지 않습니다."
        )
    target = Path(__file__).resolve().parents[2] / ".env"
    if not target.is_file() or target.is_symlink():
        raise SystemExit("저장소의 일반 .env 파일이 필요합니다.")
    label, names = (
        ("키움 REST", ("KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY"))
        if provider == "kiwoom"
        else ("Alpaca 시세", ("ALPACA_API_KEY", "ALPACA_API_SECRET"))
    )
    print(f"{label} 키를 서버 .env에 저장합니다. 입력 값은 화면에 표시되지 않습니다.")
    values = {
        names[0]: getpass.getpass("App Key: ").strip(),
        names[1]: getpass.getpass("Secret Key: ").strip(),
    }
    if any(
        not value or "\\" in value or any(ord(c) < 32 for c in value)
        for value in values.values()
    ):
        raise SystemExit(
            "빈 값·제어문자·역슬래시는 허용하지 않습니다. 다운로드한 키를 확인하세요."
        )
    lines, written = [], set()
    for line in target.read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0].strip()
        if name in values:
            if name in written:
                continue
            value = values[name].replace("'", "\\'")
            line = name + "='" + value + "'"
            written.add(name)
        lines.append(line)
    for name, value in values.items():
        if name not in written:
            value = value.replace("'", "\\'")
            lines.append(name + "='" + value + "'")
    descriptor, path = tempfile.mkstemp(prefix=".env-kiwoom-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines) + "\n")
        os.chmod(path, 0o600)
        os.replace(path, target)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print("두 키를 저장했습니다. 설정된 서버 서비스를 재시작하면 반영됩니다.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="서버에서 시세 API 키를 화면 표시 없이 입력"
    )
    parser.add_argument("--provider", choices=["kiwoom", "alpaca"], default="kiwoom")
    main(parser.parse_args().provider)
