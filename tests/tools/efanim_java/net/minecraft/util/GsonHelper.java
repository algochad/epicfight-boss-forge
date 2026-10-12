package net.minecraft.util;
import com.google.gson.*;
public class GsonHelper {
    public static JsonElement m_289747_(JsonObject o, String k) { JsonElement e = o.get(k); if (e == null) throw new JsonSyntaxException("missing " + k); return e; }
    public static JsonArray m_13924_(JsonElement e, String n) { return e.getAsJsonArray(); }
    public static String m_13906_(JsonObject o, String k) { return o.get(k).getAsString(); }
    public static boolean m_13912_(JsonObject o, String k) { return o.has(k) && o.get(k).isJsonPrimitive() && o.get(k).getAsJsonPrimitive().isString(); }
    public static float m_13915_(JsonObject o, String k) { return o.get(k).getAsFloat(); }
}
