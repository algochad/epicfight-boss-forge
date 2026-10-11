package {{PACKAGE}}.boss;

import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.EntityType;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.EntityRenderersEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import {{PACKAGE}}.boss.core.AbstractBossEntity;
import {{PACKAGE}}.boss.core.BossRenderer;

/** Client-only registration of the boss renderers. Textures live at {@code textures/entity/<boss_id>.png}. */
@Mod.EventBusSubscriber(modid = "{{MOD_ID}}", bus = Mod.EventBusSubscriber.Bus.MOD, value = Dist.CLIENT)
public final class BossClientEvents {
    private BossClientEvents() {
    }

    @SubscribeEvent
    public static void onRegisterRenderers(EntityRenderersEvent.RegisterRenderers event) {
        for (BossRegistry.BossDef def : BossRegistry.BOSSES) {
            registerRenderer(event, def.type().get(), new ResourceLocation("{{MOD_ID}}", "textures/entity/" + def.id() + ".png"));
        }
    }

    @SuppressWarnings("unchecked")
    private static void registerRenderer(EntityRenderersEvent.RegisterRenderers event,
                                         EntityType<? extends AbstractBossEntity> type, ResourceLocation texture) {
        event.registerEntityRenderer((EntityType<AbstractBossEntity>) type,
                context -> new BossRenderer<AbstractBossEntity>(context, texture));
    }
}
