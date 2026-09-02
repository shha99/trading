@echo off
REM Windows에서 run_trading_bot.py(헤드리스 매매 루프)를 켤 때 쓰는 실행기.
REM 로그온 시 자동 실행(작업 스케줄러 또는 시작프로그램 폴더)용으로 만든 것 -
REM README "헤드리스로 24시간 돌리기" 섹션의 Windows 안내 참고.
REM
REM 전제: 이 파일과 같은 폴더에 "venv"라는 이름으로 가상환경이 만들어져 있어야
REM 한다(README 2단계: python -m venv venv). 다른 이름/경로를 쓴다면 아래
REM activate 줄만 그 경로로 바꿀 것.
setlocal
cd /d "%~dp0"
call venv\Scripts\activate.bat
python run_trading_bot.py >> bot.log 2>&1
