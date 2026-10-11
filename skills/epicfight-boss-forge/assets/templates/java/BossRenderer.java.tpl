package {{PACKAGE}}.boss.core;

import net.minecraft.client.model.HumanoidModel;
import net.minecraft.client.model.geom.ModelLayers;
import net.minecraft.client.renderer.entity.EntityRendererProvider;
import net.minecraft.client.renderer.entity.HumanoidMobRenderer;
import net.minecraft.resources.ResourceLocation;

/**
 * Renderer registered for every generated boss.
 *
 * <p>It must be a {@code LivingEntityRenderer}: Epic Fight takes the entity texture from this renderer
 * ({@code getTextureLocation}) and draws its own mesh with that texture, and it maps this renderer's vanilla
 * layers (held item, head) to patched layers. When Epic Fight skips the boss the vanilla humanoid model is
 * drawn instead, so the boss never disappears.
 */
public class BossRenderer<T extends AbstractBossEntity> extends HumanoidMobRenderer<T, HumanoidModel<T>> {
    private final ResourceLocation texture;

    public BossRenderer(EntityRendererProvider.Context context, ResourceLocation texture) {
        super(context, new HumanoidModel<>(context.bakeLayer(ModelLayers.ZOMBIE)), 0.5F);
        this.texture = texture;
    }

    @Override
    public ResourceLocation getTextureLocation(T entity) {
        return texture;
    }
}
