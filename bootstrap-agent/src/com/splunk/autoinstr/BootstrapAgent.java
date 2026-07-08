package com.splunk.autoinstr;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.lang.instrument.Instrumentation;
import java.util.Properties;

/**
 * Splunk Auto-Instrumentation Bootstrap Agent
 *
 * Purpose: bridge the gap between the JVM Attach API and the OpenTelemetry Java
 * agent's configuration model.  The OTel agent reads config from system
 * properties and environment variables — neither of which can be injected into
 * a running JVM from the outside.  This agent is attached first and sets
 * system properties from a properties file before the real agent is loaded.
 *
 * Attach flow (executed by the Python daemon):
 *   1. daemon writes /tmp/splunk-autoinstr-<pid>.properties
 *   2. jattach <pid> load instrument false bootstrap-agent.jar=/tmp/splunk-autoinstr-<pid>.properties
 *      → this agentmain() runs, sets all properties via System.setProperty()
 *   3. jattach <pid> load instrument false splunk-otel-javaagent.jar
 *      → Splunk agent agentmain() runs, reads the properties we just set
 */
public class BootstrapAgent {

    public static void agentmain(String agentArgs, Instrumentation inst) {
        if (agentArgs == null || agentArgs.isEmpty()) {
            log("ERROR: no properties file path provided as agent args — nothing configured");
            return;
        }

        File propsFile = new File(agentArgs.trim());
        if (!propsFile.exists()) {
            log("ERROR: properties file not found: " + propsFile.getAbsolutePath());
            return;
        }

        Properties props = new Properties();
        try (FileInputStream fis = new FileInputStream(propsFile)) {
            props.load(fis);
        } catch (IOException e) {
            log("ERROR: failed to read " + propsFile.getAbsolutePath() + ": " + e.getMessage());
            return;
        }

        int count = 0;
        for (String key : props.stringPropertyNames()) {
            String value = props.getProperty(key);
            System.setProperty(key, value);
            count++;
        }

        log("Set " + count + " system propert" + (count == 1 ? "y" : "ies")
                + " from " + propsFile.getAbsolutePath());
    }

    // premain allows this JAR to also be used as a -javaagent at startup
    public static void premain(String agentArgs, Instrumentation inst) {
        agentmain(agentArgs, inst);
    }

    private static void log(String msg) {
        System.err.println("[splunk-autoinstr-bootstrap] " + msg);
    }
}
