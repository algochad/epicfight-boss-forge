package {{PACKAGE}}.boss.core;

import java.util.List;
import javax.annotation.Nullable;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.SwordItem;
import net.minecraft.world.item.Tier;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.level.Level;

/**
 * Weapon wielded by a generated boss and obtainable by players.
 *
 * <p>The Epic Fight weapon capability (moveset, impact, armor negation) comes from the datapack file
 * {@code data/<mod>/capabilities/weapons/<id>.json}, not from this class. On-hit effects are applied from
 * {@code BossForgeEvents} on {@code LivingHurtEvent}, because Epic Fight's attack pipeline may bypass
 * {@code Item#hurtEnemy}.
 */
public class BossWeaponItem extends SwordItem {
    private final List<EffectSpec> effects;
    private final int loreLines;
    private final String modId;
    private final String itemId;

    /**
     * @param attackDamageModifier displayed damage minus one (the player's base damage)
     * @param attackSpeedModifier  displayed attack speed minus 4.0
     */
    public BossWeaponItem(Tier tier, int attackDamageModifier, float attackSpeedModifier, Item.Properties properties,
                          List<EffectSpec> effects, int loreLines, String modId, String itemId) {
        super(tier, attackDamageModifier, attackSpeedModifier, properties);
        this.effects = effects;
        this.loreLines = loreLines;
        this.modId = modId;
        this.itemId = itemId;
    }

    public List<EffectSpec> effects() {
        return effects;
    }

    /** Applies this weapon's on-hit effects to {@code target}. */
    public void applyHitEffects(LivingEntity target, LivingEntity attacker) {
        BossAbilities.applyEffects(effects, target, attacker);
    }

    @Override
    public void appendHoverText(ItemStack stack, @Nullable Level level, List<Component> tooltip, TooltipFlag flag) {
        super.appendHoverText(stack, level, tooltip, flag);
        for (int n = 1; n <= loreLines; n++) {
            tooltip.add(Component.translatable("item." + modId + "." + itemId + ".desc." + n).withStyle(ChatFormatting.GRAY));
        }
    }
}
