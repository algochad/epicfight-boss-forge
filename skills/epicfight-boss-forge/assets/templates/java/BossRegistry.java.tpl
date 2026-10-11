package {{PACKAGE}}.boss;

import java.util.List;
import java.util.function.Supplier;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.item.CreativeModeTab;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.Rarity;
import net.minecraftforge.common.ForgeSpawnEggItem;
import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.registries.DeferredRegister;
import net.minecraftforge.registries.ForgeRegistries;
import net.minecraftforge.registries.RegistryObject;
import {{PACKAGE}}.boss.core.AbstractBossEntity;
import {{PACKAGE}}.boss.core.BossProfile;
import {{PACKAGE}}.boss.core.BossSigilItem;
import {{PACKAGE}}.boss.core.BossTier;
import {{PACKAGE}}.boss.core.BossWeaponItem;
import {{PACKAGE}}.boss.core.EffectSpec;
{{ENTITY_IMPORTS}}

/**
 * Entities, items and the creative tab of every Boss Forge boss and weapon in this mod.
 * Generated from bossforge/manifest.json on every run; edit the specs and regenerate instead of editing this file.
 */
public final class BossRegistry {
    public static final String MOD_ID = "{{MOD_ID}}";

    public static final DeferredRegister<EntityType<?>> ENTITIES = DeferredRegister.create(ForgeRegistries.ENTITY_TYPES, MOD_ID);
    public static final DeferredRegister<Item> ITEMS = DeferredRegister.create(ForgeRegistries.ITEMS, MOD_ID);
    public static final DeferredRegister<CreativeModeTab> TABS = DeferredRegister.create(Registries.CREATIVE_MODE_TAB, MOD_ID);

    /** One generated boss: its entity type, profile and the flags the mod-level event handlers need. */
    public record BossDef(String id, Supplier<? extends EntityType<? extends AbstractBossEntity>> type, BossProfile profile,
                          boolean humanoid, boolean naturalSpawn) {
    }

{{ENTITY_FIELDS}}
{{ITEM_FIELDS}}
    public static final RegistryObject<CreativeModeTab> TAB = TABS.register("bosses", () -> CreativeModeTab.builder()
            .title(Component.translatable("itemGroup." + MOD_ID + ".bosses"))
            .icon(() -> {{TAB_ICON}})
            .displayItems((parameters, output) -> {
{{TAB_ITEMS}}
            })
            .build());

    public static final List<BossDef> BOSSES = {{BOSS_DEFS}};

    private BossRegistry() {
    }

    public static void register(IEventBus bus) {
        ENTITIES.register(bus);
        ITEMS.register(bus);
        TABS.register(bus);
    }
}
