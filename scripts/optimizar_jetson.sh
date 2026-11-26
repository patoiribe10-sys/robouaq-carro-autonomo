#!/bin/bash
# optimizar_jetson.sh: aplica UNA fase de la optimizacion de la Jetson (plan del 26-nov).
#
# Por defecto solo MUESTRA los comandos (no cambia nada). Para aplicarlos: agregar --aplicar.
#   scripts/optimizar_jetson.sh F1                 # ver
#   scripts/optimizar_jetson.sh F1 --aplicar       # aplicar (usuario, sin sudo)
#   scripts/optimizar_jetson.sh F2a --aplicar      # los de sistema usan sudo: te pide la contrasena
#
# Fases: F1  autostart y servicios de USUARIO (GNOME)      F2a Docker y containerd
#        F2b packagekit y fwupd                            F2c ModemManager, rpcbind, kerneloops, lpd, cups (snap)
#        F2d geoclue, colord, switcheroo, power-profiles   F2e timers de apt/motd/update-notifier
#        F3  vm.min_free_kbytes = 256 MB
#
# NUNCA toca: ssh, tailscaled, NetworkManager, wpa_supplicant, bluetooth, avahi, nvargus-daemon, udev,
# la regla /dev/esp32, el hold del kernel, nada de NVIDIA/CUDA ni systemd-timesyncd.
# Cada fase se revierte con scripts/revertir_optimizacion.sh <fase>.
set -u
FASE="${1:-}"
APLICAR=0
[ "${2:-}" = "--aplicar" ] && APLICAR=1
S=""
[ "$(id -u)" -ne 0 ] && S="sudo"
MARCA='# creado por optimizar_jetson.sh (se puede borrar)'

run() {                                   # run <comando...>: lo muestra y, con --aplicar, lo ejecuta
    echo "  \$ $*"
    if [ "$APLICAR" -eq 1 ]; then
        eval "$@" || echo "    (aviso: termino con error, sigo)"
    fi
}

autostart_off() {                         # override del usuario que oculta un autostart del sistema
    local nombre="$1" origen="/etc/xdg/autostart/$1.desktop" destino="$HOME/.config/autostart/$1.desktop"
    [ -f "$origen" ] || { echo "  (no existe $origen, lo salto)"; return; }
    echo "  override: $destino  (Hidden=true)"
    if [ "$APLICAR" -eq 1 ]; then
        mkdir -p "$HOME/.config/autostart"
        printf '%s\n[Desktop Entry]\nType=Application\nName=%s (desactivado)\nHidden=true\nX-GNOME-Autostart-enabled=false\n' \
            "$MARCA" "$nombre" > "$destino"
    fi
}

case "$FASE" in
F1)
    echo "== F1: autostart y servicios de usuario (sin sudo) =="
    for a in gnome-software-service org.gnome.Evolution-alarm-notify update-notifier org.gnome.DejaDup.Monitor \
             print-applet onboard-autostart orca-autostart spice-vdagent ubuntu-advantage-notification \
             ubuntu-report-on-upgrade tracker-miner-fs-3; do
        autostart_off "$a"
    done
    run "systemctl --user mask --now tracker-miner-fs-3.service gnome-remote-desktop.service evolution-addressbook-factory.service evolution-calendar-factory.service evolution-source-registry.service"
    echo "  (cierra lo que ya esta corriendo en esta sesion; en el proximo inicio ya no arranca)"
    # (el nombre de un proceso se trunca a 15 caracteres: "evolution-alarm-notify" aparece como "evolution-alarm")
    for p in gnome-software evolution-alarm update-notifier; do run "pkill -x $p"; done
    ;;
F2a)
    echo "== F2a: Docker y containerd (se apagan, NO se desinstalan) =="
    run "$S systemctl disable --now docker.service docker.socket containerd.service"
    ;;
F2b)
    echo "== F2b: packagekit y fwupd =="
    run "$S systemctl mask --now packagekit.service"
    run "$S systemctl disable --now fwupd-refresh.timer"
    run "$S systemctl mask --now fwupd.service"
    ;;
F2c)
    echo "== F2c: ModemManager, rpcbind, kerneloops, lpd y el snap cups =="
    run "$S systemctl disable --now ModemManager.service rpcbind.service rpcbind.socket kerneloops.service lpd.service"
    run "$S snap stop --disable cups"
    ;;
F2d)
    echo "== F2d: geoclue, colord, switcheroo-control, power-profiles-daemon =="
    run "$S systemctl mask --now geoclue.service colord.service switcheroo-control.service power-profiles-daemon.service"
    ;;
F2e)
    echo "== F2e: timers de apt, motd y update-notifier =="
    run "$S systemctl disable --now apt-daily.timer apt-daily-upgrade.timer motd-news.timer update-notifier-download.timer update-notifier-motd.timer man-db.timer"
    ;;
F3)
    echo "== F3: reserva libre del kernel (vm.min_free_kbytes 45056 -> 262144) =="
    echo "  archivo: /etc/sysctl.d/99-carro.conf"
    if [ "$APLICAR" -eq 1 ]; then
        printf '# Reserva de 256 MB libres para que el kernel recupere cache antes y NvMap (camaras) no se quede sin memoria\nvm.min_free_kbytes = 262144\n' | $S tee /etc/sysctl.d/99-carro.conf >/dev/null
    fi
    run "$S sysctl -p /etc/sysctl.d/99-carro.conf"
    ;;
*)
    sed -n '2,16p' "$0"
    exit 1
    ;;
esac

[ "$APLICAR" -eq 1 ] && echo "== listo. Ahora mide:  scripts/medir_jetson.sh \"$FASE despues\" ==" || echo "== (simulacion: no se cambio nada; agrega --aplicar) =="
