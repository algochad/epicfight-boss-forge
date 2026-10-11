package {{PACKAGE}}.boss.core;

import java.util.List;
import net.minecraft.world.BossEvent;

/**
 * Immutable per-boss data generated from the boss spec.
 *
 * <p>{@code mainWeaponItemId} and {@code ambientSound} may be null. {@code phaseThresholds[i]} is the health
 * fraction at or below which phase {@code i + 1} starts; {@code phaseAnnounce[i]} says whether entering that
 * phase sends the translatable message {@code bossforge.<mod>.<id>.phase.<i + 1>}.
 */
public record BossProfile(
        String id,
        String modId,
        double health,
        double armor,
        double armorToughness,
        double attackDamage,
        double movementSpeed,
        double knockbackResistance,
        double followRange,
        int xpReward,
        String mainWeaponItemId,
        boolean showBar,
        BossEvent.BossBarColor barColor,
        BossEvent.BossBarOverlay barOverlay,
        float[] phaseThresholds,
        boolean[] phaseAnnounce,
        List<AbilitySpec> abilities,
        String hurtSound,
        String deathSound,
        String ambientSound) {
}
