package {{PACKAGE}}.boss.core;

import javax.annotation.Nullable;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerBossEvent;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.world.DifficultyInstance;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.MobSpawnType;
import net.minecraft.world.entity.SpawnGroupData;
import net.minecraft.world.entity.ai.attributes.AttributeSupplier;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.ai.goal.FloatGoal;
import net.minecraft.world.entity.ai.goal.LookAtPlayerGoal;
import net.minecraft.world.entity.ai.goal.RandomLookAroundGoal;
import net.minecraft.world.entity.ai.goal.WaterAvoidingRandomStrollGoal;
import net.minecraft.world.entity.ai.goal.target.HurtByTargetGoal;
import net.minecraft.world.entity.ai.goal.target.NearestAttackableTargetGoal;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.ServerLevelAccessor;
import net.minecraftforge.registries.ForgeRegistries;

/**
 * Base class of every generated boss.
 *
 * <p>It extends {@link Monster} (a {@code PathfinderMob}, which Epic Fight's custom mob patch requires). It
 * deliberately registers no melee goal: Epic Fight attaches its own attack and chase goals when it loads the
 * boss's mobpatch datapack file.
 */
public abstract class AbstractBossEntity extends Monster {
    private static final String PHASE_TAG = "BossPhase";
    private static final double MESSAGE_RANGE = 64.0D;

    private final BossProfile profile;
    @Nullable
    private final ServerBossEvent bossEvent;
    private int phase = 1;

    protected AbstractBossEntity(EntityType<? extends AbstractBossEntity> type, Level level, BossProfile profile) {
        super(type, level);
        this.profile = profile;
        this.xpReward = profile.xpReward();
        this.setPersistenceRequired();
        this.bossEvent = profile.showBar()
                ? new ServerBossEvent(this.getDisplayName(), profile.barColor(), profile.barOverlay())
                : null;
    }

    public static AttributeSupplier.Builder createAttributes(BossProfile profile) {
        return Monster.createMonsterAttributes()
                .add(Attributes.MAX_HEALTH, profile.health())
                .add(Attributes.ARMOR, profile.armor())
                .add(Attributes.ARMOR_TOUGHNESS, profile.armorToughness())
                .add(Attributes.ATTACK_DAMAGE, profile.attackDamage())
                .add(Attributes.MOVEMENT_SPEED, profile.movementSpeed())
                .add(Attributes.KNOCKBACK_RESISTANCE, profile.knockbackResistance())
                .add(Attributes.FOLLOW_RANGE, profile.followRange());
    }

    public BossProfile profile() {
        return profile;
    }

    /** Current 1-based phase. Phases only advance; healing never moves a boss back to an earlier phase. */
    public int getPhase() {
        return phase;
    }

    @Override
    protected void registerGoals() {
        this.goalSelector.addGoal(0, new FloatGoal(this));
        this.goalSelector.addGoal(5, new WaterAvoidingRandomStrollGoal(this, 1.0D));
        this.goalSelector.addGoal(6, new LookAtPlayerGoal(this, Player.class, 16.0F));
        this.goalSelector.addGoal(7, new RandomLookAroundGoal(this));
        this.targetSelector.addGoal(1, new HurtByTargetGoal(this));
        this.targetSelector.addGoal(2, new NearestAttackableTargetGoal<>(this, Player.class, true));
    }

    @Override
    public boolean removeWhenFarAway(double distanceToClosestPlayer) {
        return false;
    }

    @Nullable
    @Override
    @SuppressWarnings("deprecation")
    public SpawnGroupData finalizeSpawn(ServerLevelAccessor level, DifficultyInstance difficulty, MobSpawnType reason,
                                        @Nullable SpawnGroupData spawnData, @Nullable CompoundTag dataTag) {
        SpawnGroupData data = super.finalizeSpawn(level, difficulty, reason, spawnData, dataTag);
        equipMainWeapon();
        enterPhase(1, false);
        return data;
    }

    private void equipMainWeapon() {
        String id = profile.mainWeaponItemId();
        if (id == null) {
            return;
        }
        ResourceLocation key = ResourceLocation.tryParse(id);
        if (key == null || !ForgeRegistries.ITEMS.containsKey(key)) {
            return;
        }
        Item item = ForgeRegistries.ITEMS.getValue(key);
        this.setItemSlot(EquipmentSlot.MAINHAND, new ItemStack(item));
        this.setDropChance(EquipmentSlot.MAINHAND, 0.0F);
    }

    @Override
    protected void customServerAiStep() {
        super.customServerAiStep();
        if (bossEvent != null) {
            bossEvent.setProgress(this.getHealth() / this.getMaxHealth());
        }
        updatePhase();
        BossAbilities.tick(this, phase);
    }

    private void updatePhase() {
        float[] thresholds = profile.phaseThresholds();
        if (thresholds.length == 0) {
            return;
        }
        float ratio = this.getHealth() / this.getMaxHealth();
        int target = 1;
        for (int i = 0; i < thresholds.length; i++) {
            if (ratio <= thresholds[i]) {
                target = i + 1;
            }
        }
        while (phase < target) {
            phase++;
            enterPhase(phase, true);
        }
    }

    private void enterPhase(int entered, boolean growl) {
        if (this.level().isClientSide()) {
            return;
        }
        if (growl) {
            this.playSound(SoundEvents.ENDER_DRAGON_GROWL, 2.0F, 1.0F);
        }
        boolean[] announce = profile.phaseAnnounce();
        if (entered >= 1 && entered <= announce.length && announce[entered - 1]) {
            Component message = Component.translatable("bossforge." + profile.modId() + "." + profile.id() + ".phase." + entered);
            for (ServerPlayer player : this.level().getEntitiesOfClass(ServerPlayer.class, this.getBoundingBox().inflate(MESSAGE_RANGE))) {
                player.sendSystemMessage(message);
            }
        }
        BossAbilities.onPhase(this, entered);
    }

    @Override
    public void addAdditionalSaveData(CompoundTag tag) {
        super.addAdditionalSaveData(tag);
        tag.putInt(PHASE_TAG, phase);
    }

    @Override
    public void readAdditionalSaveData(CompoundTag tag) {
        super.readAdditionalSaveData(tag);
        if (tag.contains(PHASE_TAG)) {
            phase = Math.max(1, tag.getInt(PHASE_TAG));
        }
        BossAbilities.restore(this, phase);
    }

    @Override
    public void setCustomName(@Nullable Component name) {
        super.setCustomName(name);
        if (bossEvent != null) {
            bossEvent.setName(this.getDisplayName());
        }
    }

    @Override
    public void startSeenByPlayer(ServerPlayer player) {
        super.startSeenByPlayer(player);
        if (bossEvent != null) {
            bossEvent.addPlayer(player);
        }
    }

    @Override
    public void stopSeenByPlayer(ServerPlayer player) {
        super.stopSeenByPlayer(player);
        if (bossEvent != null) {
            bossEvent.removePlayer(player);
        }
    }

    @Nullable
    @Override
    protected SoundEvent getAmbientSound() {
        return lookupSound(profile.ambientSound());
    }

    @Override
    protected SoundEvent getHurtSound(DamageSource source) {
        SoundEvent sound = lookupSound(profile.hurtSound());
        return sound != null ? sound : super.getHurtSound(source);
    }

    @Override
    protected SoundEvent getDeathSound() {
        SoundEvent sound = lookupSound(profile.deathSound());
        return sound != null ? sound : super.getDeathSound();
    }

    @Nullable
    private static SoundEvent lookupSound(@Nullable String id) {
        if (id == null) {
            return null;
        }
        ResourceLocation key = ResourceLocation.tryParse(id);
        if (key == null || !ForgeRegistries.SOUND_EVENTS.containsKey(key)) {
            return null;
        }
        return ForgeRegistries.SOUND_EVENTS.getValue(key);
    }
}
