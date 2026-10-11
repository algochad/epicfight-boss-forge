package {{PACKAGE}}.boss;

import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.SpawnPlacements;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraftforge.event.entity.EntityAttributeCreationEvent;
import net.minecraftforge.event.entity.EntityAttributeModificationEvent;
import net.minecraftforge.event.entity.SpawnPlacementRegisterEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import {{PACKAGE}}.boss.core.AbstractBossEntity;
import yesman.epicfight.world.entity.ai.attribute.EpicFightAttributes;

/** Mod-bus registration of boss attributes (vanilla and Epic Fight) and natural spawn rules. */
@Mod.EventBusSubscriber(modid = "{{MOD_ID}}", bus = Mod.EventBusSubscriber.Bus.MOD)
public final class BossModEvents {
    private BossModEvents() {
    }

    @SubscribeEvent
    public static void onAttributeCreation(EntityAttributeCreationEvent event) {
        for (BossRegistry.BossDef def : BossRegistry.BOSSES) {
            event.put(def.type().get(), AbstractBossEntity.createAttributes(def.profile()).build());
        }
    }

    /**
     * Epic Fight registers its combat attributes only on a hardcoded list of vanilla entity types. Its custom
     * mob patch reads them without a null check, so a modded boss without them crashes with a
     * NullPointerException when the patch is applied.
     */
    @SubscribeEvent
    public static void onAttributeModification(EntityAttributeModificationEvent event) {
        for (BossRegistry.BossDef def : BossRegistry.BOSSES) {
            EntityType<? extends LivingEntity> type = def.type().get();
            event.add(type, EpicFightAttributes.WEIGHT.get());
            event.add(type, EpicFightAttributes.ARMOR_NEGATION.get());
            event.add(type, EpicFightAttributes.IMPACT.get());
            event.add(type, EpicFightAttributes.MAX_STRIKES.get());
            event.add(type, EpicFightAttributes.STUN_ARMOR.get());
            if (def.humanoid()) {
                event.add(type, EpicFightAttributes.OFFHAND_ATTACK_SPEED.get());
                event.add(type, EpicFightAttributes.OFFHAND_MAX_STRIKES.get());
                event.add(type, EpicFightAttributes.OFFHAND_ARMOR_NEGATION.get());
                event.add(type, EpicFightAttributes.OFFHAND_IMPACT.get());
            }
        }
    }

    @SubscribeEvent
    public static void onSpawnPlacements(SpawnPlacementRegisterEvent event) {
        for (BossRegistry.BossDef def : BossRegistry.BOSSES) {
            if (def.naturalSpawn()) {
                registerSpawn(event, def.type().get());
            }
        }
    }

    @SuppressWarnings("unchecked")
    private static void registerSpawn(SpawnPlacementRegisterEvent event, EntityType<?> type) {
        event.register((EntityType<Monster>) type, SpawnPlacements.Type.ON_GROUND, Heightmap.Types.MOTION_BLOCKING_NO_LEAVES,
                Monster::checkMonsterSpawnRules, SpawnPlacementRegisterEvent.Operation.REPLACE);
    }
}
