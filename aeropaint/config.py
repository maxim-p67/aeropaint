"""Все настраиваемые числа программы. Единицы: м/с², рад/с, радианы, секунды."""

from dataclasses import dataclass, field


@dataclass
class OrientationConfig:
    accel_tau: float = 1.0           # за сколько секунд наклон подтягивается по силе тяжести
    accel_still_rate: float = 0.12   # ниже этой угловой скорости геймпад считается неподвижным
    gyro_deadzone: float = 0.010     # шум гироскопа ниже этого значения отбрасывается
    px_per_rad: float = 700.0        # пикселей холста на один радиан поворота
    grip_rotation: float = 90.0      # поворот хвата: 0, 90, 180, 270 (клавиша G)

    # Плавный возврат точки к центру после бездействия.
    auto_center_delay: float = 3.0            # через сколько секунд начать
    auto_center_delay_offscreen: float = 10.0  # то же, если точка упёрлась в край
    auto_center_ramp: float = 1.0             # за сколько секунд возврат набирает силу
    auto_center_tau: float = 0.5              # скорость сближения (меньше - быстрее), 0 = выкл
    auto_center_min_rate: float = 0.05        # минимальная скорость, чтобы дойти до центра

    calibration_samples: int = 180      # сколько замеров усредняется при калибровке
    calibration_tolerance: float = 0.35  # допустимый разброс замеров


@dataclass
class ShakeConfig:
    accel_threshold: float = 6.0      # ускорение, с которого движение считается взмахом
    min_stroke_interval: float = 0.07  # быстрее - дребезг
    max_stroke_interval: float = 0.80  # медленнее - уже не встряхивание
    mix_per_stroke: float = 0.085     # прирост перемешивания за взмах (~12 взмахов до полного)
    idle_decay: float = 1.0 / 240.0   # краска расслаивается сама
    spray_decay: float = 0.018        # и расходуется при распылении
    good_mix: float = 0.75            # выше - краска ложится ровно
    warn_mix: float = 0.30            # ниже - предупреждение
    clog_mix: float = 0.12            # ниже - баллончик плюётся
    rumble_ms: int = 55               # длительность вибрации на взмах


@dataclass
class SprayConfig:
    base_radius: float = 38.0
    min_radius: float = 6.0
    max_radius: float = 170.0
    hardness: float = 0.45        # 0 - размытое облако, 1 - резкий край
    flow: float = 1.0
    spacing_ratio: float = 0.12   # шаг между отпечатками в долях радиуса
    distance_gain: float = 0.9    # как сильно удаление увеличивает пятно
    alpha_per_stamp: float = 0.055  # прозрачность одного отпечатка


@dataclass
class CanvasConfig:
    width: int = 1600
    height: int = 900
    background: tuple = (0.96, 0.95, 0.92)
    undo_depth: int = 24
    autosave_interval: float = 120.0


@dataclass
class AppConfig:
    orientation: OrientationConfig = field(default_factory=OrientationConfig)
    shake: ShakeConfig = field(default_factory=ShakeConfig)
    spray: SprayConfig = field(default_factory=SprayConfig)
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    target_fps: int = 120
    debug_overlay: bool = False


# Восемь ячеек быстрой палитры (цифры 1-8).
DEFAULT_PALETTE = [
    (0.07, 0.07, 0.08), (0.91, 0.16, 0.18), (0.98, 0.58, 0.02), (0.99, 0.85, 0.12),
    (0.16, 0.72, 0.35), (0.11, 0.45, 0.91), (0.55, 0.22, 0.79), (0.98, 0.98, 0.98),
]
