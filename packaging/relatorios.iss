; Script do Inno Setup para gerar o instalador único (Setup.exe) do painel
; de Relatórios. Instala numa pasta do usuário, sem exigir administrador.
;
; Depende de dist\RelatoriosPan\ já estar gerada (por build.ps1) antes de
; rodar `iscc relatorios.iss`. Este arquivo vive em packaging/, mas dist\ e
; installer\ ficam na raiz do projeto - por isso definimos SourceDir=".."
; (raiz do projeto). IMPORTANTE: uma vez que SourceDir é definido, OutputDir
; passa a ser resolvido relativo a SourceDir, não ao diretório do script -
; por isso OutputDir é só "installer" (= raiz + installer), e não
; "..\installer" (que apontaria um nível ACIMA da raiz).

#define MyAppName "Relatórios Pan"
#define MyShortcutName "Relatórios - PAN"
; A versão real vem sempre de build.ps1 (/DMyAppVersion=...), que lê o
; arquivo VERSION na raiz do projeto - esse fallback só é usado se o iscc
; for chamado diretamente, sem passar por build.ps1.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "RelatoriosPan.exe"

[Setup]
AppId={{A910D7D5-9FB1-4A3C-B570-6B60380FFF3C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\RelatoriosPan
DisableDirPage=no
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousAppDir=yes
SourceDir=..
OutputDir=installer
OutputBaseFilename=banco-pan-robo-relatorios
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos adicionais:"

[Files]
; O .env é criado só na primeira instalação. Em upgrades ele preserva as
; credenciais e URLs configuradas na máquina do usuário.
Source: "dist\RelatoriosPan\*"; DestDir: "{app}"; Excludes: ".env"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\RelatoriosPan\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Dirs]
Name: "{app}\logs\runs"
Name: "{app}\logs\screenshots"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{userdesktop}\{#MyShortcutName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Relatórios Pan"; Flags: postinstall nowait skipifsilent
