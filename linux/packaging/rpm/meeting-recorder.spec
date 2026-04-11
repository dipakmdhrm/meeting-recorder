Name:           meeting-recorder
Version:        @VERSION@
Release:        1%{?dist}
Summary:        Meeting recorder with AI transcription and summarization
License:        MIT
BuildArch:      noarch
Source0:        %{name}-%{version}.tar.gz

Requires:       python3 >= 3.10
Requires:       python3-pip
Requires:       python3-gobject
Requires:       gtk3
Requires:       libayatana-appindicator-gtk3
Requires:       libnotify
Requires:       ffmpeg
Requires:       pulseaudio-utils
Requires:       curl

%description
Records meetings, transcribes audio using Google Gemini or local Whisper,
and generates structured notes with Gemini or local Ollama.

Supports both cloud (Google Gemini) and local (Whisper + Ollama) processing.

%prep
%autosetup

%install
install -d %{buildroot}/opt/meeting-recorder/linux/src
cp -r linux/src/. %{buildroot}/opt/meeting-recorder/linux/src/
install -m 644 linux/requirements.txt %{buildroot}/opt/meeting-recorder/requirements.txt

install -Dm 755 linux/packaging/usr/bin/meeting-recorder \
    %{buildroot}%{_bindir}/meeting-recorder
install -Dm 644 linux/packaging/usr/share/applications/meeting-recorder.desktop \
    %{buildroot}%{_datadir}/applications/meeting-recorder.desktop

%files
%dir /opt/meeting-recorder
/opt/meeting-recorder/linux/
/opt/meeting-recorder/requirements.txt
%{_bindir}/meeting-recorder
%{_datadir}/applications/meeting-recorder.desktop

%post
python3 -m venv /opt/meeting-recorder/venv --system-site-packages
/opt/meeting-recorder/venv/bin/pip install --quiet --upgrade pip
/opt/meeting-recorder/venv/bin/pip install --quiet -r /opt/meeting-recorder/requirements.txt
mkdir -p /var/log/meeting-recorder
chmod 1777 /var/log/meeting-recorder
update-desktop-database %{_datadir}/applications 2>/dev/null || true

%preun
# $1 == 0 means full uninstall (not upgrade)
if [ "$1" -eq 0 ]; then
    if pgrep -f "meeting_recorder" >/dev/null 2>&1; then
        pkill -f "meeting_recorder" || true
        sleep 1
    fi
fi

%postun
# $1 == 0 means full uninstall (not upgrade)
if [ "$1" -eq 0 ]; then
    rm -rf /opt/meeting-recorder/venv
    rm -rf /var/log/meeting-recorder
    for home_dir in /home/*; do
        rm -f "$home_dir/.config/autostart/meeting-recorder.desktop" 2>/dev/null || true
    done
    update-desktop-database %{_datadir}/applications 2>/dev/null || true
fi

%changelog
* @CHANGELOG_DATE@ Meeting Recorder <noreply@github.com> - @VERSION@-1
- Release @VERSION@
