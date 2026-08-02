@echo off
rem 一键启动（Windows）。
rem
rem 用法：
rem   run.bat                进入数字菜单（双击本文件即可）
rem   run.bat selftest       跑离线自测
rem   run.bat eval 600519    任意子命令原样透传
rem
rem 直接跑源码，不装包、不建虚拟环境——运行时零第三方依赖。
rem 可选环境变量：TEA_PYTHON 指定解释器，TEA_HOME 指定数据目录。

setlocal

rem 判断是不是双击启动的：双击时 cmd 以 /c 形式带上本脚本名，跑完窗口会立刻消失，
rem 只有这种情况才需要 pause 停住让人看清输出。在已有的控制台里跑则不停，
rem 否则脚本被 CI 或别的脚本调用时会一直等输入。
set "DBLCLICK=0"
echo %cmdcmdline% | find /i "%~nx0" >nul && set "DBLCLICK=1"

rem 配置与数据默认落在当前工作目录，这里锁到仓库根目录（%~dp0 带尾斜杠，要去掉），
rem 否则从不同路径启动会生出好几套互不相干的配置和持仓。
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined TEA_HOME set "TEA_HOME=%ROOT%"
cd /d "%ROOT%"

rem -------------------------------------------------- 找解释器
rem 优先用启动器 py，它能挑出已装的最高版本；其次才是 PATH 里的 python。
set "PY=%TEA_PYTHON%"
if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo 找不到 Python。请先安装 Python 3.8 或更高版本：https://www.python.org/downloads/ 1>&2
    echo 安装时记得勾选 "Add Python to PATH"。 1>&2
    goto :fail
)

rem 版本别只看名字，python 也可能是 3.7。
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
if errorlevel 1 (
    echo Python 版本过低，本项目需要 3.8+。当前： 1>&2
    %PY% -V 1>&2
    goto :fail
)

rem -------------------------------------------------- 启动
%PY% -m tea %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" if "%DBLCLICK%"=="1" pause
endlocal & exit /b %CODE%

:fail
if "%DBLCLICK%"=="1" pause
endlocal & exit /b 1
