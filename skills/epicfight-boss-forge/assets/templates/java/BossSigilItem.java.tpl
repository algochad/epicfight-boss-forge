package {{PACKAGE}}.boss.core;

import java.util.function.Supplier;
import javax.annotation.Nullable;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.MobSpawnType;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.context.UseOnContext;
import net.minecraft.world.level.Level;
import net.minecraftforge.registries.ForgeRegistries;

/** Summons its boss when used on the required block (and in the required dimension, when set). */
public class BossSigilItem extends Item {
    private final Supplier<? extends EntityType<? extends AbstractBossEntity>> bossType;
    @Nullable
    private final String requiredBlock;
    @Nullable
    private final String dimension;
    private final boolean consume;
    private final String modId;

    public BossSigilItem(Item.Properties properties, Supplier<? extends EntityType<? extends AbstractBossEntity>> bossType,
                         @Nullable String requiredBlock, @Nullable String dimension, boolean consume, String modId) {
        super(properties);
        this.bossType = bossType;
        this.requiredBlock = requiredBlock;
        this.dimension = dimension;
        this.consume = consume;
        this.modId = modId;
    }

    @Override
    public InteractionResult useOn(UseOnContext context) {
        Level level = context.getLevel();
        Player player = context.getPlayer();
        BlockPos pos = context.getClickedPos();
        if (requiredBlock != null) {
            ResourceLocation blockId = ForgeRegistries.BLOCKS.getKey(level.getBlockState(pos).getBlock());
            if (blockId == null || !blockId.toString().equals(requiredBlock)) {
                reject(level, player, "wrong_block");
                return InteractionResult.FAIL;
            }
        }
        if (dimension != null && !level.dimension().location().toString().equals(dimension)) {
            reject(level, player, "wrong_dimension");
            return InteractionResult.FAIL;
        }
        if (level instanceof ServerLevel serverLevel) {
            ItemStack stack = context.getItemInHand();
            AbstractBossEntity boss = bossType.get().spawn(serverLevel, stack, player, pos.above(), MobSpawnType.EVENT, true, false);
            if (boss == null) {
                return InteractionResult.FAIL;
            }
            if (consume && (player == null || !player.isCreative())) {
                stack.shrink(1);
            }
        }
        return InteractionResult.sidedSuccess(level.isClientSide());
    }

    private void reject(Level level, @Nullable Player player, String reason) {
        if (!level.isClientSide() && player != null) {
            player.displayClientMessage(Component.translatable("message." + modId + ".sigil." + reason), true);
        }
    }
}
