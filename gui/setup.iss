; Inno Setup skripta za namestitveni paket DN-PREVZEMNICA-Tomato GUI.
; Prevesti (compile) z ISCC.exe (Inno Setup 6+), glej build_installer.bat.
; Predpogoj: dist\DN-PREVZEMNICA-Tomato-GUI.exe mora že obstajati
; (zgrajen z build_exe.bat / gui\gui.spec).

#define MyAppName "Slonček"
#define MyAppVersion "1.0"
#define MyAppPublisher "Digit"
#define MyAppExeName "DN-PREVZEMNICA-Tomato-GUI.exe"

[Setup]
AppId={{9E7F2C9E-9E1A-4B7B-8A2B-6C9C7E9E7A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\DN-PREVZEMNICA_Tomato
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Namestitveni jezik: samo angleščina (en sam vnos v [Languages] spodaj -
; brez izbirnega dialoga za jezik, namestitev vedno teče v EN).
ShowLanguageDialog=no
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir=dist_installer
OutputBaseFilename=DN-PREVZEMNICA-Tomato-GUI-Setup
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\DN-PREVZEMNICA-Tomato-GUI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion
; .env se vedno prepiše z vrednostmi iz gui\.env, kot je bila ta datoteka
; nastavljena na razvijalčevem računalniku v trenutku generiranja tega
; namestitvenega paketa (vključno z API_KEY) - namerno, na uporabnikovo
; željo, da ni potrebno ročno vnašanje API ključa po namestitvi.
Source: ".env"; DestDir: "{app}"; DestName: ".env"; Flags: ignoreversion
; Visual C++ Redistributable (x64) - vgrajen v namestitveni paket (offline),
; da namestitev na ciljnem računalniku ne potrebuje internetne povezave.
; Prenese se ga med build-om, glej build_installer.bat.
Source: "redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Configure connection (.env)"; Filename: "notepad.exe"; Parameters: """{app}\.env"""
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "notepad.exe"; Parameters: """{app}\.env"""; Description: "Edit connection settings (.env) now"; Flags: postinstall skipifsilent unchecked
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent unchecked

; --- Runtime odvisnost: Microsoft Visual C++ Redistributable (x64) ---
; PySide6/Qt6 na Windows potrebuje ta runtime; na marsikaterem sistemu je
; že prisoten (npr. zaradi drugih aplikacij ali Windows Update). Namestitveni
; program ga nosi s seboj (glej [Files] zgoraj) - namestitev ne potrebuje
; internetne povezave. Spodnji [Code] razdelek ga zažene SAMO, če registrski
; ključ pokaže, da ga (še) ni - "če je že nameščen, drugič ni potrebno".
[Code]
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
begin
  TempFile := ExpandConstant('{tmp}\vc_redist.x64.exe');
  if not Exec(TempFile, '/install /quiet /norestart', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
    MsgBox('Could not run the Visual C++ Redistributable installer automatically. ' +
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
