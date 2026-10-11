package {{PACKAGE}}.boss.core;

import com.mojang.logging.LogUtils;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import javax.annotation.Nullable;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.effect.MobEffect;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.MobSpawnType;
import net.minecraft.world.entity.ai.attributes.AttributeInstance;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.phys.AABB;
import net.minecraftforge.registries.ForgeRegistries;
import org.slf4j.Logger;

/** Runtime behaviour of the boss abilities declared in the boss spec. */
public final class BossAbilities {
    private static final Logger LOGGER = LogUtils.getLogger();
    private static final Set<String> WARNED = new HashSet<>();

    private BossAbilities() {
    }

    /** Called every server tick of a living boss. Handles {@code aura} and {@code regen}. */
    public static void tick(AbstractBossEntity boss, int phase) {
        if (boss.level().isClientSide()) {
            return;
        }
        for (AbilitySpec ability : boss.profile().abilities()) {
            if (!ability.activeIn(phase)) {
                continue;
            }
            switch (ability.type()) {
                case AURA -> tickAura(boss, ability);
                case REGEN -> {
                    if (boss.tickCount % 20 == 0 && boss.getHealth() < boss.getMaxHealth()) {
                        boss.heal(ability.perSecond());
                    }
                }
                default -> {
                }
            }
        }
    }

    /** Called once when a boss enters {@code phase}. Handles {@code summon} and {@code enrage}. */
    public static void onPhase(AbstractBossEntity boss, int phase) {
        List<AbilitySpec> abilities = boss.profile().abilities();
        for (int i = 0; i < abilities.size(); i++) {
            AbilitySpec ability = abilities.get(i);
            if (ability.phase() != phase) {
                continue;
            }
            if (ability.type() == AbilitySpec.Type.SUMMON) {
                summon(boss, ability);
            } else if (ability.type() == AbilitySpec.Type.ENRAGE) {
                applyEnrage(boss, i, ability);
            }
        }
    }

    /** Re-applies the enrage modifiers of every phase already reached; transient modifiers are lost on reload. */
    public static void restore(AbstractBossEntity boss, int phase) {
        List<AbilitySpec> abilities = boss.profile().abilities();
        for (int i = 0; i < abilities.size(); i++) {
            AbilitySpec ability = abilities.get(i);
            if (ability.type() == AbilitySpec.Type.ENRAGE && ability.phase() <= phase) {
                applyEnrage(boss, i, ability);
            }
        }
    }

    /** Applies the {@code on_hit_effect} abilities of {@code boss} to {@code victim}. */
    public static void onBossHit(AbstractBossEntity boss, LivingEntity victim) {
        for (AbilitySpec ability : boss.profile().abilities()) {
            if (ability.type() == AbilitySpec.Type.ON_HIT_EFFECT && ability.activeIn(boss.getPhase())) {
                applyEffect(ability.effect(), ability.duration(), ability.amplifier(), ability.chance(), victim, boss);
            }
        }
    }

    /** Applies weapon effects to {@code target}, each with its own chance. */
    public static void applyEffects(List<EffectSpec> effects, LivingEntity target, @Nullable Entity source) {
        for (EffectSpec effect : effects) {
            applyEffect(effect.effect(), effect.duration(), effect.amplifier(), effect.chance(), target, source);
        }
    }

    private static void applyEffect(String id, int duration, int amplifier, float chance, LivingEntity target, @Nullable Entity source) {
        if (target.getRandom().nextFloat() >= chance) {
            return;
        }
        MobEffect effect = findEffect(id);
        if (effect != null) {
            target.addEffect(new MobEffectInstance(effect, duration, amplifier), source);
        }
    }

    @Nullable
    private static MobEffect findEffect(String id) {
        ResourceLocation key = ResourceLocation.tryParse(id);
        if (key == null || !ForgeRegistries.MOB_EFFECTS.containsKey(key)) {
            warnOnce("effect:" + id, "Unknown mob effect '{}' in a boss ability or weapon; it is ignored.", id);
            return null;
        }
        return ForgeRegistries.MOB_EFFECTS.getValue(key);
    }

    private static void warnOnce(String key, String message, String arg) {
        if (WARNED.add(key)) {
            LOGGER.warn(message, arg);
        }
    }

    private static void tickAura(AbstractBossEntity boss, AbilitySpec ability) {
        if (ability.interval() <= 0 || boss.tickCount % ability.interval() != 0) {
            return;
        }
        MobEffect effect = findEffect(ability.effect());
        if (effect == null) {
            return;
        }
        double radius = ability.radius();
        AABB area = boss.getBoundingBox().inflate(radius);
        for (Player player : boss.level().getEntitiesOfClass(Player.class, area)) {
            if (player.isAlive() && !player.isSpectator() && player.distanceToSqr(boss) <= radius * radius) {
                player.addEffect(new MobEffectInstance(effect, ability.duration(), ability.amplifier()), boss);
            }
        }
    }

    private static void summon(AbstractBossEntity boss, AbilitySpec ability) {
        if (!(boss.level() instanceof ServerLevel level)) {
            return;
        }
        ResourceLocation key = ResourceLocation.tryParse(ability.entity());
        if (key == null || !ForgeRegistries.ENTITY_TYPES.containsKey(key)) {
            warnOnce("entity:" + ability.entity(), "Boss summon skipped: entity '{}' is not registered.", ability.entity());
            return;
        }
        EntityType<?> type = ForgeRegistries.ENTITY_TYPES.getValue(key);
        for (int i = 0; i < ability.count(); i++) {
            double angle = boss.getRandom().nextDouble() * Math.PI * 2.0D;
            BlockPos pos = BlockPos.containing(
                    boss.getX() + Math.cos(angle) * ability.radius(),
                    boss.getY(),
                    boss.getZ() + Math.sin(angle) * ability.radius());
            Entity spawned = type.spawn(level, pos, MobSpawnType.MOB_SUMMONED);
            if (spawned instanceof Mob mob && boss.getTarget() != null) {
                mob.setTarget(boss.getTarget());
            }
        }
    }

    private static void applyEnrage(AbstractBossEntity boss, int index, AbilitySpec ability) {
        addModifier(boss, boss.getAttribute(Attributes.MOVEMENT_SPEED), index, "speed", ability.speedPct());
        addModifier(boss, boss.getAttribute(Attributes.ATTACK_DAMAGE), index, "damage", ability.damagePct());
    }

    private static void addModifier(AbstractBossEntity boss, @Nullable AttributeInstance attribute, int index, String kind, float percent) {
        if (attribute == null) {
            return;
        }
        BossProfile profile = boss.profile();
        UUID uuid = UUID.nameUUIDFromBytes(
                (profile.modId() + ":" + profile.id() + ":enrage:" + index + ":" + kind).getBytes(StandardCharsets.UTF_8));
        if (attribute.getModifier(uuid) == null) {
            attribute.addTransientModifier(new AttributeModifier(
                    uuid, "bossforge enrage " + kind, percent / 100.0D, AttributeModifier.Operation.MULTIPLY_BASE));
        }
    }
}
