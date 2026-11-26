import evdev

# 1. Función matemática para mapear los valores (Interpolación Lineal)
def mapear_angulo(valor_joystick, in_min=0, in_max=65535, out_min=0, out_max=180):
    # Aplicamos la fórmula y convertimos el resultado a un número entero
    angulo = (valor_joystick - in_min) * (out_max - out_min) / (in_max - in_min) + out_min
    return int(angulo)

# 2. Conexión al control de Xbox
dispositivos = [evdev.InputDevice(ruta) for ruta in evdev.list_devices()]
control_xbox = None

for dispositivo in dispositivos:
    if "Xbox" in dispositivo.name or "Microsoft" in dispositivo.name:
        control_xbox = dispositivo
        break

if control_xbox is None:
    print("❌ No se encontró el control de Xbox.")
    exit()

print(f"✅ Conectado a: {control_xbox.name}")
print("🎮 Mueve el Joystick Izquierdo (Izquierda/Derecha)...")

# 3. Lectura y Mapeo en Tiempo Real
try:
    for evento in control_xbox.read_loop():
        # Filtramos solo los movimientos analógicos (joysticks)
        if evento.type == evdev.ecodes.EV_ABS:
            
            # ABS_X es el código exacto del movimiento horizontal del joystick izquierdo
            if evento.code == evdev.ecodes.ABS_X:
                valor_bruto = evento.value
                
                # Traducimos el valor del joystick a grados
                grados_servo = mapear_angulo(valor_bruto)
                
                print(f"Joystick: {valor_bruto:6d}  =>  Servo Ackerman: {grados_servo:3d}°")

except KeyboardInterrupt:
    print("\n🛑 Programa terminado.")