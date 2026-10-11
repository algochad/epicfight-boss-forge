package {{PACKAGE}}.boss.{{BOSS_ID}};

import java.util.List;
import net.minecraft.world.BossEvent;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.level.Level;
import {{PACKAGE}}.boss.core.AbilitySpec;
import {{PACKAGE}}.boss.core.AbstractBossEntity;
import {{PACKAGE}}.boss.core.BossProfile;

/** The {{BOSS_ID}} boss. Generated from bossforge/bosses/{{BOSS_ID}}.boss.json; regenerate instead of editing. */
public class {{CLASS}}Entity extends AbstractBossEntity {
    public static final BossProfile PROFILE = new BossProfile(
{{PROFILE_ARGS}});

    public {{CLASS}}Entity(EntityType<{{CLASS}}Entity> type, Level level) {
        super(type, level, PROFILE);
    }
}
