package {{PACKAGE}}.boss.core;

import net.minecraft.world.item.Tier;
import net.minecraft.world.item.crafting.Ingredient;

/**
 * Tier used by {@link BossWeaponItem}. The attack damage bonus is 0 because the item's own damage modifier
 * ({@code damage - 1}) already carries the weapon damage; the durability comes from the weapon spec.
 */
public final class BossTier implements Tier {
    private final int uses;

    public BossTier(int uses) {
        this.uses = uses;
    }

    @Override
    public int getUses() {
        return uses;
    }

    @Override
    public float getSpeed() {
        return 6.0F;
    }

    @Override
    public float getAttackDamageBonus() {
        return 0.0F;
    }

    @Override
    public int getLevel() {
        return 3;
    }

    @Override
    public int getEnchantmentValue() {
        return 15;
    }

    @Override
    public Ingredient getRepairIngredient() {
        return Ingredient.EMPTY;
    }
}
