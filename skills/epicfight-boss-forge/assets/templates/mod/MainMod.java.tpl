package {{PACKAGE}};

import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;
import {{PACKAGE}}.boss.BossRegistry;

@Mod({{CLASS}}.MOD_ID)
public class {{CLASS}} {
    public static final String MOD_ID = "{{MOD_ID}}";

    public {{CLASS}}() {
        IEventBus modEventBus = FMLJavaModLoadingContext.get().getModEventBus();
        BossRegistry.register(modEventBus);
    }
}
