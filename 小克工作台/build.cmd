@echo off
rem Builds the app with the C# compiler that ships with Windows (.NET Framework 4.x). No SDK needed.
setlocal
chcp 65001 >nul
set FW=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319
set OUT=%~dp0小克工作台.exe
"%FW%\csc.exe" /nologo /codepage:65001 /target:winexe /platform:x64 /optimize+ ^
  /out:"%OUT%" /win32icon:"%~dp0app.ico" /resource:"%~dp0app.ico",app.ico ^
  /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll ^
  /r:"%~dp0Microsoft.Web.WebView2.Core.dll" /r:"%~dp0Microsoft.Web.WebView2.WinForms.dll" ^
  /r:"%FW%\WPF\UIAutomationClient.dll" /r:"%FW%\WPF\UIAutomationTypes.dll" /r:"%FW%\WPF\WindowsBase.dll" ^
  "%~dp0src\*.cs"
if errorlevel 1 (echo BUILD FAILED & exit /b 1)
echo Built %OUT%