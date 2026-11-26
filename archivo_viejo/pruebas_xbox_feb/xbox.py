import evdev

# Busca automáticamente todos los dispositivos conectados
dispositivos = [evdev.InputDevice(ruta) for ruta in evdev.list_devices()]
control_xbox = None

# Filtra para encontrar el de Xbox
for dispositivo in dispositivos:
    if "Xbox" in dispositivo.name or "Microsoft" in dispositivo.name:
        control_xbox = dispositivo
        break

if control_xbox is None:
    print("❌ No se encontró el control de Xbox. ¿Está encendido y conectado?")
    exit()

print(f"✅ ¡Conectado a: {control_xbox.name}!")
print("🎮 Mueve los joysticks o presiona los botones (Presiona Ctrl+C para salir)...")

# Bucle infinito para leer el control en tiempo real
try:
    for evento in control_xbox.read_loop():
        # Si presionas un botón
        if evento.type == evdev.ecodes.EV_KEY:
            print(f"Botón {evento.code} | Estado: {'Presionado' if evento.value == 1 else 'Suelto'}")
            
        # Si mueves un joystick o gatillo (ejes analógicos)
        elif evento.type == evdev.ecodes.EV_ABS:
            print(f"Eje {evento.code} | Valor: {evento.value}")

except KeyboardInterrupt:
    print("\n🛑 Programa terminado por el usuario.")