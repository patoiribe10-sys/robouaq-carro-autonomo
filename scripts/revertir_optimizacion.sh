#!/bin/bash
# revertir_optimizacion.sh: deshace UNA fase de optimizar_jetson.sh.
#
# Por defecto solo MUESTRA los comandos. Para aplicarlos: agregar --aplicar.
#   scripts/revertir_optimizacion.sh F2a --aplicar
# Fases: F1 F2a F2b F2c F2d F2e F3   (F4 es el modo nvpmodel: ver el CLAUDE.md, no se cambio)
set -u
FASE="${1:-}"
APLICAR=0
[ "${2:-}" = "--aplicar" ] && APLICAR=1
S=""
[ "$(id -u)" -ne 0 ] && S="sudo"

run() {
    echo "  \$ $*"
    if [ "$APLICAR" -eq 1 ]; then
        eval "$@" || echo "    (aviso: termino con error, sigo)"
    fi
}

case "$FASE" in
F1)
    echo "== revertir F1 =="
    # solo se borran los overrides que creo optimizar_jetson.sh (llevan la marca)
    for f in "$HOME"/.config/autostart/*.desktop; do
        [ -f "$f" ] && grep -q "creado por optimizar_jetson.sh" "$f" && run "rm -f '$f'"
    done
    run "systemctl --user unmask tracker-miner-fs-3.service gnome-remote-desktop.service evolution-addressbook-factory.service evolution-calendar-factory.service evolution-source-registry.service"
    echo "  (cierra sesion y vuelve a entrar para que arranquen los autostart)"
    ;;
F2a)
    echo "== revertir F2a =="
    run "$S systemctl enable --now docker.socket docker.service containerd.service"
    ;;
F2b)
    echo "== revertir F2b =="
    run "$S systemctl unmask packagekit.service fwupd.service"
    run "$S systemctl enable --now fwupd-refresh.timer"
    ;;
F2c)
    echo "== revertir F2c =="
    run "$S systemctl enable --now ModemManager.service rpcbind.socket rpcbind.service kerneloops.service lpd.service"
    run "$S snap start --enable cups"
    ;;
F2d)
    echo "== revertir F2d =="
    run "$S systemctl unmask geoclue.service colord.service switcheroo-control.service power-profiles-daemon.service"
    run "$S systemctl start geoclue.service colord.service switcheroo-control.service power-profiles-daemon.service"
    ;;
F2e)
    echo "== revertir F2e =="
    run "$S systemctl enable --now apt-daily.timer apt-daily-upgrade.timer motd-news.timer update-notifier-download.timer update-notifier-motd.timer man-db.timer"
    ;;
F3)
    echo "== revertir F3 =="
    run "$S rm -f /etc/sysctl.d/99-carro.conf"
    run "$S sysctl -w vm.min_free_kbytes=45056"
    ;;
*)
    sed -n '2,7p' "$0"
    exit 1
    ;;
esac

[ "$APLICAR" -eq 1 ] && echo "== revertido. Mide:  scripts/medir_jetson.sh \"$FASE revertida\" ==" || echo "== (simulacion: no se cambio nada; agrega --aplicar) =="
