import com.google.gson.*;
import yesman.epicfight.api.animation.*;
import yesman.epicfight.api.asset.JsonAssetLoader;
import yesman.epicfight.api.utils.math.OpenMatrix4f;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

/** Runs the REAL Epic Fight 20.14.17 classes (from the mod jar) on biped.json + an animation json and prints, per
 * requested time, the 20 skinning matrices (pose * toOrigin, Minecraft space, row-major) as JSON. */
public class Harness {
    static Map<String, TransformSheet> sheets = new LinkedHashMap<>();

    static void pose(Joint joint, OpenMatrix4f parent, Map<String, JointTransform> pose, Map<String, OpenMatrix4f> out) {
        JointTransform jt = pose.getOrDefault(joint.getName(), JointTransform.empty());
        OpenMatrix4f result = jt.getAnimationBoundMatrix(joint, parent);
        OpenMatrix4f res = new OpenMatrix4f(result);
        res.mulBack(joint.getToOrigin());
        out.put(joint.getName(), res);
        for (Joint sub : joint.getSubJoints()) pose(sub, result, pose, out);
    }

    static String rows(OpenMatrix4f m) {
        // OpenMatrix4f stores m_colrow (LWJGL naming): m01 = column 0 row 1
        float[][] r = {
            {m.m00, m.m10, m.m20, m.m30}, {m.m01, m.m11, m.m21, m.m31},
            {m.m02, m.m12, m.m22, m.m32}, {m.m03, m.m13, m.m23, m.m33}};
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) { if (i + j > 0) sb.append(","); sb.append(r[i][j]); }
        return sb.append("]").toString();
    }

    public static void main(String[] args) throws Exception {
        JsonObject mesh = JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
        JsonObject anim = JsonParser.parseString(Files.readString(Path.of(args[1]))).getAsJsonObject();
        JsonObject arm = mesh.getAsJsonObject("armature");
        JsonObject hier = arm.getAsJsonArray("hierarchy").get(0).getAsJsonObject();
        JsonArray joints = arm.getAsJsonArray("joints");
        Map<String, Integer> idx = new HashMap<>();
        int c = 0;
        for (JsonElement e : joints) { String n = e.getAsString(); if (!n.equals("Coord")) idx.put(n, c++); }
        JsonAssetLoader.TransformFormat afmt = JsonAssetLoader.getAsTransformFormatOrDefault(arm, "armature_format");
        Map<String, Joint> byName = new HashMap<>();
        Method gj = JsonAssetLoader.class.getDeclaredMethod("getJoint", JsonObject.class, Map.class, Map.class, JsonAssetLoader.TransformFormat.class, boolean.class);
        gj.setAccessible(true);
        Joint root = (Joint) gj.invoke(null, hier, idx, byName, afmt, true);
        root.initOriginTransform(new OpenMatrix4f());

        JsonAssetLoader.TransformFormat fmt = JsonAssetLoader.getAsTransformFormatOrDefault(anim, "format");
        boolean first = true;
        for (JsonElement el : anim.getAsJsonArray("animation")) {
            JsonObject e = el.getAsJsonObject();
            String name = e.get("name").getAsString();
            if (name.equals("Coord")) { first = false; continue; }
            Joint j = byName.get(name);
            if (j == null) continue;
            sheets.put(name, JsonAssetLoader.getTransformSheet(e, OpenMatrix4f.invert(j.getLocalTransform(), null), first, fmt));
            first = false;
        }
        StringBuilder sb = new StringBuilder("[");
        boolean firstT = true;
        for (int a = 2; a < args.length; a++) {
            float t = Float.parseFloat(args[a]);
            Map<String, JointTransform> pose = new HashMap<>();
            for (Map.Entry<String, TransformSheet> en : sheets.entrySet()) pose.put(en.getKey(), en.getValue().getInterpolatedTransform(t));
            Map<String, OpenMatrix4f> out = new LinkedHashMap<>();
            pose(root, new OpenMatrix4f(), pose, out);
            if (!firstT) sb.append(","); firstT = false;
            sb.append("{\"t\":").append(t).append(",\"m\":{");
            boolean f = true;
            for (Map.Entry<String, OpenMatrix4f> en : out.entrySet()) { if (!f) sb.append(","); f = false; sb.append("\"").append(en.getKey()).append("\":").append(rows(en.getValue())); }
            sb.append("}}");
        }
        System.out.println(sb.append("]"));
    }
}
