# 처음 설정

1. LG와 Dell의 Windows 영상 연결을 유지하고 Windows 앱을 실행합니다. 기존 설치의 매핑·연결 키는 자동으로 가져옵니다.
2. 처음 설치한 PC라면 앱의 **입력 매핑 파일 열기**로 `%LOCALAPPDATA%\MonitorSwitch\relay-monitors.json`을 엽니다. `relay-monitors.example.json`의 형식으로 두 모니터를 설정하세요. ID는 `python scripts/windows_nvidia_probe.py`의 읽기 전용 결과에서 확인합니다. 모델명만으로 ID를 추측하지 마세요.
3. 앱을 다시 실행합니다. Windows 트레이에서 **맥 연결 파일 내보내기**를 선택합니다. Mac에서 **윈도우 연결 가져오기**로 연결합니다. 연결 파일에는 비밀 키가 있으므로 공개하지 마세요.
4. 두 PC를 같은 사설망 또는 같은 Tailscale 네트워크에 연결합니다. Windows 방화벽은 앱의 TCP 55661 수신을 사설망 또는 Tailscale 연결에 허용해야 합니다. 인터넷에 포트를 공개하지 마세요.

Dell 예시: 표준 DDC, Mac HDMI 1 `17`, Windows DP 1 `15`. LG 대체 프로토콜 예시: Mac HDMI 1 `144`, Windows DP 1 `208`. 실제 케이블·모델에서 확인한 값만 사용하세요. LG는 NVIDIA DP 경로로 제어하며, 모델별 동작은 다를 수 있습니다.

OSD로 수동 전환할 수 있는 상태에서 시험하세요. 응답이 없거나 결과가 불확실하면 자동 재전송하지 않습니다. 다른 PC 앱 또는 모니터 OSD로 돌아오세요.

USB 자동 전환은 Windows 중계의 실행 중 이력을 공유합니다. 마지막 정상 전송과 입력이 같으면 해당 모니터의 전환 명령을 생략합니다. 현재 화면을 직접 확인한 것으로 간주하지 않으며, 모니터 자체 메뉴의 변경은 추적하지 않습니다. 앱을 재시작하면 이력을 초기화하고, 앱의 전환 버튼을 직접 누르면 항상 명령을 다시 보냅니다.

앱은 사용자 폴더에 설치됩니다. Mac은 `~/Applications/Monitor Switch.app`, Windows는 `%LOCALAPPDATA%\MonitorSwitch\app`입니다. 설정은 설치 파일과 별도 보관되며 업데이트 후에도 유지됩니다. 이전 프로그램은 설정 폴더의 `Previous Installs`에 남습니다.
