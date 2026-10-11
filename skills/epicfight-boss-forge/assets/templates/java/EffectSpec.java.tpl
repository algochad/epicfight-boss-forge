package {{PACKAGE}}.boss.core;

/**
 * A mob effect applied by a weapon hit or a boss ability.
 *
 * @param effect    registry id of the effect, for example {@code minecraft:slowness}
 * @param duration  duration in ticks
 * @param amplifier effect amplifier (0 = level I)
 * @param chance    probability in 0..1 that the effect is applied on a hit
 */
public record EffectSpec(String effect, int duration, int amplifier, float chance) {
}
