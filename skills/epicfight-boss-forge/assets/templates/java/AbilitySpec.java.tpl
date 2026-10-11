package {{PACKAGE}}.boss.core;

/**
 * One boss ability. Use the static factories; unused components keep neutral values.
 * An empty {@code phases} array means the ability is active in every phase.
 */
public record AbilitySpec(
        Type type,
        String effect,
        String entity,
        int duration,
        int amplifier,
        float chance,
        float radius,
        int interval,
        int count,
        int phase,
        float perSecond,
        float speedPct,
        float damagePct,
        int[] phases) {

    public enum Type {
        ON_HIT_EFFECT,
        AURA,
        SUMMON,
        REGEN,
        ENRAGE
    }

    /** Applies an effect to whatever the boss hurts. */
    public static AbilitySpec onHitEffect(String effect, int duration, int amplifier, float chance, int... phases) {
        return new AbilitySpec(Type.ON_HIT_EFFECT, effect, null, duration, amplifier, chance, 0.0F, 0, 0, 0, 0.0F, 0.0F, 0.0F, phases);
    }

    /** Every {@code interval} ticks applies an effect to players within {@code radius} blocks. */
    public static AbilitySpec aura(String effect, float radius, int interval, int duration, int amplifier, int... phases) {
        return new AbilitySpec(Type.AURA, effect, null, duration, amplifier, 1.0F, radius, interval, 0, 0, 0.0F, 0.0F, 0.0F, phases);
    }

    /** Once per boss life, when entering {@code phase}, spawns {@code count} copies of {@code entity} around the boss. */
    public static AbilitySpec summon(String entity, int count, int phase, float radius) {
        return new AbilitySpec(Type.SUMMON, null, entity, 0, 0, 1.0F, radius, 0, count, phase, 0.0F, 0.0F, 0.0F, new int[0]);
    }

    /** Heals {@code perSecond} health points once per second. */
    public static AbilitySpec regen(float perSecond, int... phases) {
        return new AbilitySpec(Type.REGEN, null, null, 0, 0, 1.0F, 0.0F, 0, 0, 0, perSecond, 0.0F, 0.0F, phases);
    }

    /** On entering {@code phase}, raises movement speed and attack damage by the given percentages. */
    public static AbilitySpec enrage(int phase, float speedPct, float damagePct) {
        return new AbilitySpec(Type.ENRAGE, null, null, 0, 0, 1.0F, 0.0F, 0, 0, phase, 0.0F, speedPct, damagePct, new int[0]);
    }

    /** True when this ability is active in the given 1-based phase. */
    public boolean activeIn(int currentPhase) {
        if (phases.length == 0) {
            return true;
        }
        for (int p : phases) {
            if (p == currentPhase) {
                return true;
            }
        }
        return false;
    }
}
