; Inno Setup skripta za namestitveni paket DN-PREVZEMNICA-Tomato GUI.
; Prevesti (compile) z ISCC.exe (Inno Setup 6+), glej build_installer.bat.
; Predpogoj: dist\DN-PREVZEMNICA-Tomato-GUI.exe mora že obstajati
; (zgrajen z build_exe.bat / gui\gui.spec).

#define MyAppName "DN-PREVZEMNICA-Tomato GUI"
#define MyAppVersion "1.0"
#define MyAppPublisher "Digit"
#define MyAppExeName "DN-PREVZEMNICA-Tomato-GUI.exe"

[Setup]
AppId={{9E7F2C9E-9E1A-4B7B-8A2B-6C9C7E9E7A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\DN-PREVZEMNICA-Tomato-GUI
DefaultGroupName=DN-PREVZEMNICA-Tomato
DisableProgramGroupPage=yes
; Namestitveni jezik: samo angleščina (en sam vnos v [Languages] spodaj -
; brez izbirnega dialoga za jezik, namestitev vedno teče v EN).
ShowLanguageDialog=no
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
OutputDir=..\dist_installer
OutputBaseFilename=DN-PREVZEMNICA-Tomato-GUI-Setup
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\DN-PREVZEMNICA-Tomato-GUI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion
; .env se namesti samo, če v ciljni mapi še ne obstaja (Flags: onlyifdoesntexist) -
; tako ponovna namestitev/nadgradnja ne prepiše uporabnikove obstoječe konfiguracije
; (API_BASE_URL/API_KEY).
Source: ".env.example"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Configure connection (.env)"; Filename: "notepad.exe"; Parameters: """{app}\.env"""
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "notepad.exe"; Parameters: """{app}\.env"""; Description: "Edit connection settings (.env) now"; Flags: postinstall skipifsilent unchecked
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent unchecked

; --- Runtime odvisnost: Microsoft Visual C++ Redistributable (x64) ---
; PySide6/Qt6 na Windows potrebuje ta runtime; na marsikaterem sistemu je
; že prisoten (npr. zaradi drugih aplikacij ali Windows Update). Spodnji
; [Code] razdelek ga namesti SAMO, če registrski ključ pokaže, da ga (še)
; ni - "če že niso [nameščene], drugič pa ni potrebno".
[Code]
function URLDownloadToFile(pCaller: Integer; szURL, szFileName: string; Reserved: Integer; lpfnCB: Integer): Integer;
  external 'URLDownloadToFileA@urlmon.dll stdcall';

function VCRedistInstalled(): Boolean;
var
  installed: Cardinal;
begin
  Result := False;
  if RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64', 'Installed', installed) then
    Result := (installed = 1);
end;

procedure InstallVCRedist();
var
  ResultCode: Integer;
  TempFile: string;
  DownloadOk: Integer;
begin
  TempFile := ExpandConstant('{tmp}\vc_redist.x64.exe');
  DownloadOk := URLDownloadToFile(0, 'https://aka.ms/vs/17/release/vc_redist.x64.exe', TempFile, 0, 0);
  if DownloadOk = 0 then
  begin
    if not Exec(TempFile, '/install /quiet /norestart', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      MsgBox('Could not run the Visual C++ Redistributable installer automatically. ' +
             'Please install it manually from https://aka.ms/vs/17/release/vc_redist.x64.exe',
             mbError, MB_OK);
  end
  else
    MsgBox('Could not download the Visual C++ Redistributable automatically (no internet access?). ' +
           'Please install it manually from https://aka.ms/vs/17/release/vc_redist.x64.exe',
           mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not VCRedistInstalled() then
      InstallVCRedist();
  end;
end;
