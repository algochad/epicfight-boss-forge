package {{PACKAGE}}.boss;

import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraftforge.event.entity.living.LivingHurtEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import {{PACKAGE}}.boss.core.AbstractBossEntity;
import {{PACKAGE}}.boss.core.BossAbilities;
import {{PACKAGE}}.boss.core.BossWeaponItem;

/** Forge-bus handlers: boss {@code on_hit_effect} abilities and {@link BossWeaponItem} on-hit effects. */
@Mod.EventBusSubscriber(modid = "{{MOD_ID}}", bus = Mod.EventBusSubscriber.Bus.FORGE)
public final class BossForgeEvents {
    private BossForgeEvents() {
    }

    @SubscribeEvent
    public static void onLivingHurt(LivingHurtEvent event) {
        Entity source = event.getSource().getEntity();
        if (!(source instanceof LivingEntity attacker) || attacker.level().isClientSide()) {
            return;
        }
        LivingEntity victim = event.getEntity();
        if (attacker instanceof AbstractBossEntity boss) {
            BossAbilities.onBossHit(boss, victim);
        }
        ItemStack held = attacker.getMainHandItem();
        if (held.getItem() instanceof BossWeaponItem weapon) {
            weapon.applyHitEffects(victim, attacker);
        }
    }
}
