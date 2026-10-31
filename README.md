# Monitor Switch

Mac 메뉴 막대와 Windows 트레이에서 Dell·LG 모니터 입력을 전환합니다. 무료 오픈 소스 앱입니다.

1. [최신 릴리즈](https://github.com/codeyoma/monitor-switch/releases/latest)에서 Mac 또는 Windows ZIP을 받습니다.
2. 압축을 풀고 앱을 실행합니다. Windows는 `app` 폴더 전체를 유지하세요. 기존 앱은 먼저 종료하세요.
3. Windows에서 연결 파일을 내보내고 Mac에서 가져옵니다. [처음 설정](WINDOWS-RELAY-SETUP.md)을 참고하세요.

로그인 시 자동 실행하며, 새 버전은 시작 30초 후와 6시간마다 확인합니다. 서명을 검증하고 작업이 끝나면 자동 업데이트합니다. 설정·연결 키는 유지됩니다. 활동 기록에서 자동 업데이트를 끄거나 직접 확인할 수 있습니다.

배포: Apple Silicon Mac, Windows x64. 별도 Python 설치가 필요 없습니다. 현재 앱은 공인 인증서로 서명·공증되지 않았습니다. macOS 보안 경고가 나오면 시스템 설정에서 개발자와 출처를 확인한 뒤 열어야 할 수 있습니다.

DDC 지원은 모니터·케이블·독에 따라 다릅니다. LG 모델 전체의 호환성을 보장하지 않습니다. 비활성 입력의 DDC가 막히면 연결된 Windows 중계를 사용합니다. USB 자동 전환은 장치 연결 이벤트를 사용합니다.

소스 실행: Python 3.11 이상에서 `pip install -r requirements.txt`, `python app.py`. Mac 직접 DDC는 `bash scripts/setup-mac.sh`로 준비합니다. 빌드: `pip install -r requirements-build.txt`, `python scripts/build.py`. 버전을 올리고 `vX.Y.Z` 태그를 게시하면 GitHub Actions가 양쪽 앱과 서명된 업데이트를 배포합니다.

[MIT](LICENSE) · [타사 라이선스](THIRD_PARTY.md)
